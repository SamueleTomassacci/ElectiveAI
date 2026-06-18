import copy
import json
import math
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

import rclpy
from geometry_msgs.msg import Pose, PoseStamped, Quaternion
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import MarkerArray

from empower_ros2.plan_parser import parse_symbolic_plan
from empower_ros2.primitive_actions import make_nav_goal_pose, navigate


NAVIGATE = "NAVIGATE"
GRAB = "GRAB"
DROP = "DROP"
UNIMPLEMENTED_ACTIONS = {"PUSH", "PULL"}
SIMULATED_OBJECTS = ("book", "computer", "peluche")


class LowLevelExecutionNode(Node):
    """ROS2 port of EMPOWER low_level_execution.py for NAVIGATE-only execution."""

    def __init__(self):
        super().__init__("empower_low_level_execution")
        self.declare_parameter("use_case", "person_table_child_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("plan_json_path", "")
        self.declare_parameter("plan_txt_path", "")
        self.declare_parameter("centroids_topic", "/pcl_centroids")
        self.declare_parameter("names_topic", "/pcl_names")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter("marker_timeout_sec", 30.0)
        self.declare_parameter("tf_timeout_sec", 2.0)
        self.declare_parameter("action_server_timeout_sec", 20.0)
        self.declare_parameter("nav_result_timeout_sec", 300.0)
        self.declare_parameter("standoff_distance", 0.7)
        self.declare_parameter("continue_on_nav_failure", False)
        self.declare_parameter("object_simulation.enabled", False)
        self.declare_parameter("object_simulation.drop_distance", 0.75)
        self.declare_parameter("object_simulation.world_frame", "world")
        self.declare_parameter("object_simulation.robot_base_frame", "base_footprint")
        self.declare_parameter("object_simulation.gz_command", "gz")
        self.declare_parameter("object_simulation.gz_world_name", "person_table_child_room")
        self.declare_parameter("object_simulation.gz_cli_timeout_sec", 10.0)
        self.declare_parameter("object_simulation.world_sdf_path", "")
        self.declare_parameter(
            "object_simulation.objects.book.gazebo_entity",
            "clear_red_book_on_right_table",
        )
        self.declare_parameter("object_simulation.objects.book.drop_z", 0.02)
        self.declare_parameter(
            "object_simulation.objects.computer.gazebo_entity",
            "laptop_computer_on_right_table",
        )
        self.declare_parameter("object_simulation.objects.computer.drop_z", 0.02)
        self.declare_parameter(
            "object_simulation.objects.peluche.gazebo_entity",
            "teddy_bear_on_right_table",
        )
        self.declare_parameter("object_simulation.objects.peluche.drop_z", 0.04)

        self._centroids = None
        self._names = None
        self.held_object = None
        self.object_simulation_enabled = bool(
            self.get_parameter("object_simulation.enabled").value
        )
        self.object_mapping = self._load_object_simulation_mapping()
        self.object_sdf_xml = {}
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            self.get_parameter("nav_action_name").value,
        )
        if self.object_simulation_enabled:
            self._cache_object_sdf_xml()
        self.create_subscription(
            MarkerArray,
            self.get_parameter("centroids_topic").value,
            self._centroids_callback,
            10,
        )
        self.create_subscription(
            MarkerArray,
            self.get_parameter("names_topic").value,
            self._names_callback,
            10,
        )
        try:
            self._run()
        finally:
            rclpy.shutdown()

    def _centroids_callback(self, msg):
        self._centroids = msg

    def _names_callback(self, msg):
        self._names = msg

    def _run(self):
        plan = self._load_plan()
        if not plan:
            self.get_logger().warn("No symbolic plan steps found; nothing to execute")
            return

        if any(str(step.get("action", "")).upper() == NAVIGATE for step in plan):
            objects = self._wait_for_objects()
            if not objects:
                self.get_logger().error("No EMPOWER object centroids available")
                return

            self.get_logger().info(
                "Loaded EMPOWER object centroids: " + ", ".join(sorted(objects.keys()))
            )
        else:
            self.get_logger().info(
                "Plan has no NAVIGATE steps; skipping EMPOWER centroid wait"
            )
        for step in plan:
            action = str(step.get("action", "")).upper()
            description = step.get("description") or self._format_step(step)
            if action == NAVIGATE:
                ok = self._execute_navigate(step)
                if not ok and not self.get_parameter("continue_on_nav_failure").value:
                    self.get_logger().error("Stopping low-level execution after NAVIGATE failure")
                    return
            elif action == GRAB:
                self._execute_grab(step)
            elif action == DROP:
                self._execute_drop(step)
            elif action in UNIMPLEMENTED_ACTIONS:
                self.get_logger().warn(
                    f"Skipping {description}: {action} is not implemented in this MVP"
                )
            else:
                self.get_logger().warn(f"Skipping {description}: unknown action")

    def _load_object_simulation_mapping(self):
        mapping = {}
        for symbolic_name in SIMULATED_OBJECTS:
            entity = str(
                self.get_parameter(
                    f"object_simulation.objects.{symbolic_name}.gazebo_entity"
                ).value
            ).strip()
            drop_z = float(
                self.get_parameter(f"object_simulation.objects.{symbolic_name}.drop_z").value
            )
            mapping[symbolic_name] = {
                "symbolic_name": symbolic_name,
                "gazebo_entity": entity,
                "drop_z": drop_z,
            }
        return mapping

    def _cache_object_sdf_xml(self):
        world_sdf_path = self._resolve_world_sdf_path(
            self.get_parameter("object_simulation.world_sdf_path").value
        )
        if not world_sdf_path:
            self.get_logger().error(
                "Object simulation is enabled, but object_simulation.world_sdf_path is empty"
            )
            return
        if not os.path.exists(world_sdf_path):
            self.get_logger().error(f"Object simulation world SDF not found: {world_sdf_path}")
            return

        try:
            tree = ET.parse(world_sdf_path)
        except ET.ParseError as exc:
            self.get_logger().error(f"Failed to parse world SDF {world_sdf_path}: {exc}")
            return

        models = {
            model.attrib.get("name"): model
            for model in tree.getroot().findall(".//model")
            if model.attrib.get("name")
        }
        for symbolic_name, object_info in self.object_mapping.items():
            gazebo_entity = object_info["gazebo_entity"]
            model = models.get(gazebo_entity)
            if model is None:
                self.get_logger().error(
                    f"No <model> named '{gazebo_entity}' found in {world_sdf_path}"
                )
                continue
            model_copy = copy.deepcopy(model)
            self._neutralize_model_pose(model_copy)
            model_xml = ET.tostring(model_copy, encoding="unicode")
            self.object_sdf_xml[gazebo_entity] = f'<sdf version="1.6">{model_xml}</sdf>'
            self.get_logger().info(f"Cached SDF XML for {symbolic_name} -> {gazebo_entity}")

    def _resolve_world_sdf_path(self, configured_path):
        path = str(configured_path).strip()
        if not path or os.path.exists(path):
            return path

        candidates = []
        exchange_prefix = "/root/exchange/"
        if path.startswith(exchange_prefix):
            suffix = path[len(exchange_prefix) :]
            candidates.append(os.path.join(os.getcwd(), suffix))
            candidates.append(os.path.join(os.getcwd(), "exchange", os.path.basename(path)))

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
        candidates.append(os.path.join(repo_root, "exchange", os.path.basename(path)))

        for candidate in candidates:
            if os.path.exists(candidate):
                self.get_logger().info(
                    f"Resolved object_simulation.world_sdf_path '{path}' to '{candidate}'"
                )
                return candidate
        return path

    def _neutralize_model_pose(self, model):
        for child in list(model):
            if child.tag == "pose":
                child.text = "0 0 0 0 0 0"
                return
        pose = ET.Element("pose")
        pose.text = "0 0 0 0 0 0"
        model.insert(0, pose)

    def _execute_grab(self, step):
        target = self._manipulation_target(step)
        normalized = self._normalize_manipulation_target(target)
        self.get_logger().info(f"GRAB target received='{target}', normalized='{normalized}'")
        if not self.object_simulation_enabled:
            self.get_logger().info(
                "Skipping GRAB: object_simulation.enabled is false; physical manipulation "
                "is not implemented"
            )
            return False
        if normalized not in self.object_mapping:
            self.get_logger().warn(
                f"Cannot GRAB unknown target '{target}'. Known targets: "
                + ", ".join(SIMULATED_OBJECTS)
            )
            return False
        if self.held_object is not None:
            self.get_logger().warn(
                "Cannot GRAB while already holding "
                f"{self.held_object['symbolic_name']} ({self.held_object['gazebo_entity']})"
            )
            return False

        object_info = self.object_mapping[normalized]
        gazebo_entity = object_info["gazebo_entity"]
        sdf_xml = self.object_sdf_xml.get(gazebo_entity)
        self.get_logger().info(f"GRAB {normalized}: resolved Gazebo entity '{gazebo_entity}'")
        if not sdf_xml:
            self.get_logger().error(
                f"Cannot GRAB {normalized}: no cached SDF XML for '{gazebo_entity}'"
            )
            return False
        if not self._delete_with_gz_cli(gazebo_entity):
            return False

        self.held_object = {**object_info, "sdf_xml": sdf_xml}
        self.get_logger().info(f"GRAB {normalized} -> deleted Gazebo entity {gazebo_entity}")
        self.get_logger().info(
            f"Current held object: {self.held_object['symbolic_name']} "
            f"({self.held_object['gazebo_entity']})"
        )
        return True

    def _execute_drop(self, step):
        target = self._manipulation_target(step)
        normalized = self._normalize_manipulation_target(target)
        self.get_logger().info(f"DROP target received='{target}', normalized='{normalized}'")
        if not self.object_simulation_enabled:
            self.get_logger().info(
                "Skipping DROP: object_simulation.enabled is false; physical manipulation "
                "is not implemented"
            )
            return False
        if normalized not in self.object_mapping:
            self.get_logger().warn(
                f"Cannot DROP unknown target '{target}'. Known targets: "
                + ", ".join(SIMULATED_OBJECTS)
            )
            return False
        if self.held_object is None:
            self.get_logger().warn("Cannot DROP: no object is currently held")
            return False
        if normalized != self.held_object["symbolic_name"]:
            self.get_logger().warn(
                f"Cannot DROP {normalized}: currently holding {self.held_object['symbolic_name']}"
            )
            return False
        drop_pose = self._compute_drop_pose(self.held_object)
        if drop_pose is None:
            return False

        if not self._spawn_with_gz_cli(self.held_object, drop_pose):
            return False

        dropped = self.held_object
        self.held_object = None
        self.get_logger().info(
            f"DROP {normalized} -> spawned {dropped['gazebo_entity']} in front of robot"
        )
        self.get_logger().info("Current held object: none")
        return True

    def _manipulation_target(self, step):
        if step.get("target"):
            return str(step["target"])
        description = str(step.get("description", "")).strip()
        return re.sub(r"^\s*\w+\s+", "", description).strip()

    def _normalize_manipulation_target(self, target):
        normalized = str(target).lower().strip()
        normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return re.sub(r"^the\s+", "", normalized)

    def _gz_model_base_command(self):
        command = [
            str(self.get_parameter("object_simulation.gz_command").value),
            "model",
        ]
        world_name = str(self.get_parameter("object_simulation.gz_world_name").value).strip()
        if world_name:
            command.extend(["-w", world_name])
        return command

    def _run_gz_command(self, command, label):
        timeout_sec = float(self.get_parameter("object_simulation.gz_cli_timeout_sec").value)
        self.get_logger().info(f"{label} Gazebo command: {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_sec,
            )
        except FileNotFoundError:
            self.get_logger().error(
                f"{label} failed: '{command[0]}' command was not found"
            )
            return False
        except subprocess.TimeoutExpired:
            self.get_logger().error(
                f"{label} Gazebo command timed out after {timeout_sec:.1f}s"
            )
            return False

        if result.stdout.strip():
            self.get_logger().info(f"{label} Gazebo stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            self.get_logger().warn(f"{label} Gazebo stderr: {result.stderr.strip()}")
        if result.returncode != 0:
            self.get_logger().error(
                f"{label} Gazebo command failed with exit code {result.returncode}"
            )
            return False
        self.get_logger().info(f"{label} Gazebo command succeeded")
        return True

    def _delete_with_gz_cli(self, gazebo_entity):
        command = self._gz_model_base_command()
        command.extend(["-m", gazebo_entity, "-d"])
        self.get_logger().info(f"Deleting Gazebo model '{gazebo_entity}'")
        return self._run_gz_command(command, "DeleteEntity")

    def _spawn_with_gz_cli(self, held_object, drop_pose):
        yaw = self._yaw_from_quaternion(drop_pose.orientation)
        spawn_sdf_xml = self._sdf_xml_with_model_pose(
            held_object["sdf_xml"],
            drop_pose.position.x,
            drop_pose.position.y,
            drop_pose.position.z,
            yaw,
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".sdf",
                prefix=f"{held_object['symbolic_name']}_",
                delete=False,
            ) as sdf_file:
                sdf_file.write(spawn_sdf_xml)
                temp_path = sdf_file.name

            command = self._gz_model_base_command()
            command.extend(
                [
                    "-m",
                    held_object["gazebo_entity"],
                    "-f",
                    temp_path,
                ]
            )
            self.get_logger().info(f"Spawning Gazebo model '{held_object['gazebo_entity']}'")
            return self._run_gz_command(command, "SpawnEntity")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as exc:
                    self.get_logger().warn(f"Failed to remove temporary SDF {temp_path}: {exc}")

    def _sdf_xml_with_model_pose(self, sdf_xml, x, y, z, yaw):
        try:
            root = ET.fromstring(sdf_xml)
        except ET.ParseError as exc:
            self.get_logger().warn(
                f"Failed to update spawn SDF pose, using cached SDF XML unchanged: {exc}"
            )
            return sdf_xml

        model = root.find("model")
        if model is None:
            self.get_logger().warn("Spawn SDF has no <model>, using cached SDF XML unchanged")
            return sdf_xml

        pose = model.find("pose")
        if pose is None:
            pose = ET.Element("pose")
            model.insert(0, pose)
        pose.text = f"{x:.6f} {y:.6f} {z:.6f} 0 0 {yaw:.6f}"
        return ET.tostring(root, encoding="unicode")

    def _compute_drop_pose(self, held_object):
        world_frame = str(self.get_parameter("object_simulation.world_frame").value)
        robot_base_frame = str(self.get_parameter("object_simulation.robot_base_frame").value)
        robot_pose = self._current_robot_pose_for_drop(world_frame, robot_base_frame)
        if robot_pose is None:
            self.get_logger().error(
                f"Cannot DROP {held_object['symbolic_name']}: TF pose could not be obtained"
            )
            return None

        yaw = self._yaw_from_quaternion(robot_pose.pose.orientation)
        drop_distance = float(self.get_parameter("object_simulation.drop_distance").value)
        drop_pose = Pose()
        drop_pose.position.x = robot_pose.pose.position.x + drop_distance * math.cos(yaw)
        drop_pose.position.y = robot_pose.pose.position.y + drop_distance * math.sin(yaw)
        drop_pose.position.z = float(held_object["drop_z"])
        drop_pose.orientation = self._quaternion_from_yaw(yaw)
        self.get_logger().info(
            f"Computed DROP pose for {held_object['symbolic_name']}: "
            f"robot=({robot_pose.pose.position.x:.3f}, {robot_pose.pose.position.y:.3f}, "
            f"yaw={yaw:.3f}), drop=({drop_pose.position.x:.3f}, "
            f"{drop_pose.position.y:.3f}, {drop_pose.position.z:.3f})"
        )
        return drop_pose

    def _current_robot_pose_for_drop(self, preferred_frame, robot_base_frame):
        frames = []
        for frame in (preferred_frame, self.get_parameter("map_frame").value):
            if frame and frame not in frames:
                frames.append(frame)
        for frame in frames:
            try:
                transform = self.tf_buffer.lookup_transform(
                    frame,
                    robot_base_frame,
                    Time(),
                    timeout=Duration(seconds=float(self.get_parameter("tf_timeout_sec").value)),
                )
            except TransformException as exc:
                self.get_logger().warn(
                    f"Cannot read robot pose from TF {frame}->{robot_base_frame}: {exc}"
                )
                continue
            if frame != preferred_frame:
                self.get_logger().warn(
                    f"Using fallback TF frame '{frame}' for DROP pose; assuming it is aligned "
                    f"with Gazebo frame '{preferred_frame}'"
                )
            pose = PoseStamped()
            pose.header.frame_id = frame
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = transform.transform.translation.x
            pose.pose.position.y = transform.transform.translation.y
            pose.pose.position.z = transform.transform.translation.z
            pose.pose.orientation = transform.transform.rotation
            return pose
        return None

    def _load_plan(self):
        dump_dir = self._dump_dir()
        plan_json_path = self.get_parameter("plan_json_path").value
        plan_txt_path = self.get_parameter("plan_txt_path").value
        if not plan_json_path:
            plan_json_path = os.path.join(dump_dir, "planning.json")
        if not plan_txt_path:
            plan_txt_path = os.path.join(dump_dir, "planning.txt")

        if os.path.exists(plan_json_path):
            with open(plan_json_path, "r", encoding="utf-8") as plan_file:
                plan = json.load(plan_file)
            self.get_logger().info(f"Loaded parsed plan from {plan_json_path}")
            return plan

        if os.path.exists(plan_txt_path):
            with open(plan_txt_path, "r", encoding="utf-8") as plan_file:
                plan_text = plan_file.read()
            self.get_logger().info(f"Loaded symbolic plan text from {plan_txt_path}")
            return parse_symbolic_plan(plan_text)

        raise FileNotFoundError(f"No EMPOWER plan found at {plan_json_path} or {plan_txt_path}")

    def _dump_dir(self):
        return os.path.join(
            self.get_parameter("output_root").value,
            "output",
            self.get_parameter("use_case").value,
        )

    def _wait_for_objects(self):
        timeout_sec = float(self.get_parameter("marker_timeout_sec").value)
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and time.monotonic() < deadline:
            if self._centroids is not None and self._names is not None:
                objects = self._objects_from_markers(self._names, self._centroids)
                if objects:
                    return objects
            rclpy.spin_once(self, timeout_sec=0.1)
        return {}

    def _objects_from_markers(self, name_list, centroid_list):
        objects = {}
        map_frame = self.get_parameter("map_frame").value
        for name_marker, centroid_marker in zip(name_list.markers, centroid_list.markers):
            label = name_marker.text.strip()
            if not label:
                continue
            try:
                pose = self._marker_to_pose_stamped(centroid_marker, map_frame)
            except RuntimeError as exc:
                self.get_logger().warn(str(exc), throttle_duration_sec=2.0)
                return {}
            map_marker = copy.deepcopy(centroid_marker)
            map_marker.header.frame_id = map_frame
            map_marker.pose.position = pose.pose.position
            map_marker.pose.orientation = pose.pose.orientation
            if label in objects:
                self.get_logger().warn(
                    f"Duplicate EMPOWER label '{label}', keeping the latest marker",
                    throttle_duration_sec=5.0,
                )
            objects[label] = map_marker
        self._objects = objects
        return objects

    def _execute_navigate(self, step):
        target_label, marker = self._resolve_target_marker(step)
        if marker is None:
            self.get_logger().error(
                f"Cannot execute NAVIGATE: target '{step.get('target')}' was not found"
            )
            return False

        try:
            target_pose = self._marker_to_pose_stamped(marker, self.get_parameter("map_frame").value)
        except RuntimeError as exc:
            self.get_logger().error(str(exc))
            return False
        robot_pose = self._current_robot_pose()
        nav_goal = make_nav_goal_pose(
            target_pose,
            robot_pose,
            float(self.get_parameter("standoff_distance").value),
        )
        nav_goal.header.stamp = self.get_clock().now().to_msg()
        self.get_logger().info(
            f"Executing NAVIGATE to '{target_label}' from EMPOWER centroid "
            f"({target_pose.pose.position.x:.3f}, {target_pose.pose.position.y:.3f})"
        )
        return navigate(
            self,
            self.nav_client,
            nav_goal,
            float(self.get_parameter("action_server_timeout_sec").value),
            float(self.get_parameter("nav_result_timeout_sec").value),
        )

    def _resolve_target_marker(self, step):
        normalized_objects = {
            self._normalize_label(label): (label, marker)
            for label, marker in getattr(self, "_objects", {}).items()
        }
        for candidate in self._target_candidates(step):
            normalized = self._normalize_label(candidate)
            if normalized in normalized_objects:
                return normalized_objects[normalized]
        return None, None

    def _target_candidates(self, step):
        fields = []
        for key in ("target", "reference"):
            value = step.get(key)
            if value:
                fields.append(str(value))
        description = step.get("description")
        if description:
            fields.append(re.sub(r"^\s*\w+\s+", "", str(description)).strip())

        candidates = []
        for field in fields:
            cleaned = re.sub(r"^to\s+", "", field.strip(), flags=re.IGNORECASE)
            candidates.append(cleaned)
            tokens = cleaned.split()
            for length in range(len(tokens), 0, -1):
                for start in range(0, len(tokens) - length + 1):
                    candidates.append(" ".join(tokens[start : start + length]))

        deduped = []
        seen = set()
        for candidate in candidates:
            normalized = self._normalize_label(candidate)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(candidate)
        return deduped

    def _normalize_label(self, label):
        label = str(label).lower().strip()
        label = re.sub(r"[^a-z0-9 ]+", " ", label)
        label = re.sub(r"\b(the|a|an)\b", " ", label)
        return re.sub(r"\s+", " ", label).strip()

    def _marker_to_pose_stamped(self, marker, target_frame):
        pose = PoseStamped()
        source_frame = marker.header.frame_id or target_frame
        pose.header.frame_id = source_frame
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = marker.pose.position.x
        pose.pose.position.y = marker.pose.position.y
        pose.pose.position.z = marker.pose.position.z
        pose.pose.orientation.w = 1.0
        if source_frame == target_frame:
            return pose

        timeout = Duration(seconds=float(self.get_parameter("tf_timeout_sec").value))
        try:
            transform = self.tf_buffer.lookup_transform(
                target_frame,
                source_frame,
                Time(),
                timeout=timeout,
            )
        except TransformException as exc:
            raise RuntimeError(f"Failed TF transform {source_frame}->{target_frame}: {exc}") from exc

        x, y, z = self._apply_transform(
            (pose.pose.position.x, pose.pose.position.y, pose.pose.position.z),
            transform,
        )
        pose.header.frame_id = target_frame
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        return pose

    def _current_robot_pose(self):
        map_frame = self.get_parameter("map_frame").value
        base_frame = self.get_parameter("base_frame").value
        try:
            transform = self.tf_buffer.lookup_transform(
                map_frame,
                base_frame,
                Time(),
                timeout=Duration(seconds=float(self.get_parameter("tf_timeout_sec").value)),
            )
        except TransformException as exc:
            self.get_logger().warn(
                f"Cannot read robot pose from TF {map_frame}->{base_frame}: {exc}"
            )
            return None
        pose = PoseStamped()
        pose.header.frame_id = map_frame
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = transform.transform.translation.x
        pose.pose.position.y = transform.transform.translation.y
        pose.pose.position.z = transform.transform.translation.z
        pose.pose.orientation = transform.transform.rotation
        return pose

    def _apply_transform(self, point, transform):
        rotated = self._rotate_vector(point, transform.transform.rotation)
        translation = transform.transform.translation
        return (
            rotated[0] + translation.x,
            rotated[1] + translation.y,
            rotated[2] + translation.z,
        )

    def _rotate_vector(self, vector, quaternion):
        x = quaternion.x
        y = quaternion.y
        z = quaternion.z
        w = quaternion.w
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        if norm == 0.0:
            return vector
        x /= norm
        y /= norm
        z /= norm
        w /= norm

        vx, vy, vz = vector
        uvx = y * vz - z * vy
        uvy = z * vx - x * vz
        uvz = x * vy - y * vx
        uuvx = y * uvz - z * uvy
        uuvy = z * uvx - x * uvz
        uuvz = x * uvy - y * uvx
        return (
            vx + 2.0 * (w * uvx + uuvx),
            vy + 2.0 * (w * uvy + uuvy),
            vz + 2.0 * (w * uvz + uuvz),
        )

    def _yaw_from_quaternion(self, quaternion):
        x = quaternion.x
        y = quaternion.y
        z = quaternion.z
        w = quaternion.w
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        return math.atan2(siny_cosp, cosy_cosp)

    def _quaternion_from_yaw(self, yaw):
        quaternion = Quaternion()
        quaternion.z = math.sin(yaw / 2.0)
        quaternion.w = math.cos(yaw / 2.0)
        return quaternion

    def _format_step(self, step):
        parts = [str(step.get("action", ""))]
        if step.get("target"):
            parts.append(str(step["target"]))
        if step.get("reference"):
            parts.append(str(step["reference"]))
        return " ".join(parts).strip()


def main(args=None):
    rclpy.init(args=args)
    LowLevelExecutionNode()


if __name__ == "__main__":
    main()

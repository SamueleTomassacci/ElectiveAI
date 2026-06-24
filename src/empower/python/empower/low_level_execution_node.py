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

from empower.plan_parser import parse_symbolic_plan
from empower.primitive_actions import make_nav_goal_pose, navigate
from empower.static_scene_graph import StaticSceneGraph, StaticSceneGraphError, default_scene_graph_path


NAVIGATE = "NAVIGATE"
GRAB = "GRAB"
DROP = "DROP"
UNIMPLEMENTED_ACTIONS = {"PUSH", "PULL"}
SIMULATED_OBJECTS = ("book", "computer", "peluche")


class LowLevelExecutionNode(Node):
    #Implementation of low level actions

    def __init__(self):
        super().__init__("empower_low_level_execution")
        self.declare_parameter("use_case", "therapy_task")
        self.declare_parameter("output_root", "/root/exchange/output")
        self.declare_parameter("plan_json_path", "")
        self.declare_parameter("plan_txt_path", "")
        self.declare_parameter("centroids_topic", "/pcl_centroids")
        self.declare_parameter("names_topic", "/pcl_names")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("static_scene_graph_path", default_scene_graph_path())
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
        self.declare_parameter("object_simulation.hidden_x", -3.8)
        self.declare_parameter("object_simulation.hidden_y", 2.4)
        self.declare_parameter("object_simulation.hidden_z", 0.02)
        self.declare_parameter("object_simulation.world_frame", "world")
        self.declare_parameter("object_simulation.robot_base_frame", "base_footprint")
        self.declare_parameter("object_simulation.fixed_drop.enabled", False)
        self.declare_parameter("object_simulation.fixed_drop.x", 0.0)
        self.declare_parameter("object_simulation.fixed_drop.y", 1.8)
        self.declare_parameter("object_simulation.fixed_drop.z", -1.0)
        self.declare_parameter("object_simulation.fixed_drop.yaw", 1.5708)
        self.declare_parameter("object_simulation.gz_command", "gz")
        self.declare_parameter("object_simulation.gz_world_name", "scene")
        self.declare_parameter("object_simulation.gz_cli_timeout_sec", 10.0)
        self.declare_parameter("object_simulation.world_sdf_path", "")
        self.declare_parameter(
            "object_simulation.objects.book.gazebo_entity",
            "clear_red_book_on_right_table",
        )
        self.declare_parameter("object_simulation.objects.book.aliases", ["book"])
        self.declare_parameter("object_simulation.objects.book.drop_z", 0.02)
        self.declare_parameter(
            "object_simulation.objects.computer.gazebo_entity",
            "laptop_computer_on_right_table",
        )
        self.declare_parameter(
            "object_simulation.objects.computer.aliases",
            ["computer", "laptop", "laptop computer"],
        )
        self.declare_parameter("object_simulation.objects.computer.drop_z", 0.12)
        self.declare_parameter(
            "object_simulation.objects.peluche.gazebo_entity",
            "teddy_bear_on_right_table",
        )
        self.declare_parameter(
            "object_simulation.objects.peluche.aliases",
            ["peluche", "teddy bear", "plush toy", "stuffed animal"],
        )
        self.declare_parameter("object_simulation.objects.peluche.drop_z", 0.04)

        self._centroids = None
        self._names = None
        self.static_scene_graph = self.load_static_scene_graph()
        self.held_object = None
        self.last_navigation_target_is_child = False
        self.object_simulation_enabled = bool(
            self.get_parameter("object_simulation.enabled").value
        )
        self.object_mapping = self.load_object_simulation_mapping()
        self.object_aliases = self.load_object_aliases()
        self.object_sdf_xml = {}
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            self.get_parameter("nav_action_name").value,
        )
        if self.object_simulation_enabled:
            self.cache_object_sdf_xml()
        self.global_objects = self.load_global_detections()
        self.create_subscription(
            MarkerArray,
            self.get_parameter("centroids_topic").value,
            self.centroids_callback,
            10,
        )
        self.create_subscription(
            MarkerArray,
            self.get_parameter("names_topic").value,
            self.names_callback,
            10,
        )
        try:
            self.run()
        finally:
            rclpy.shutdown()

    def centroids_callback(self, msg):
        self._centroids = msg

    def names_callback(self, msg):
        self._names = msg

    def load_static_scene_graph(self):
        path = str(self.get_parameter("static_scene_graph_path").value).strip()
        if not path:
            raise RuntimeError("static_scene_graph_path is empty")
        if not os.path.exists(path):
            raise RuntimeError(f"Static scene graph not found at {path}")
        try:
            graph = StaticSceneGraph.from_json(path)
        except (OSError, StaticSceneGraphError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot load static scene graph {path}: {exc}") from exc
        if graph.frame_id != str(self.get_parameter("map_frame").value):
            raise RuntimeError(
                f"Static scene graph frame '{graph.frame_id}' differs from map_frame "
                f"'{self.get_parameter('map_frame').value}'"
            )
        self.get_logger().info(f"Loaded static scene graph: {path}")
        return graph

    def run(self):
        plan = self.load_plan()
        if not plan:
            self.get_logger().warn("No symbolic plan steps found; nothing to execute")
            return

        nav_steps = [
            step
            for step in plan
            if str(step.get("action", "")).upper() == NAVIGATE
        ]
        if any(
            not self.is_static_scene_navigation_step(step)
            and not self.can_resolve_global_navigation_step(step)
            for step in nav_steps
        ):
            objects = self.wait_for_objects()
            if not objects:
                self.get_logger().error("No EMPOWER object centroids available")
                return

            self.get_logger().info(
                "Loaded EMPOWER object centroids: " + ", ".join(sorted(objects.keys()))
            )
        else:
            self.get_logger().info(
                "Plan has no visual NAVIGATE steps; skipping EMPOWER centroid wait"
            )
        for step in plan:
            action = str(step.get("action", "")).upper()
            description = step.get("description") or self.format_step(step)
            if action == NAVIGATE:
                ok = self.execute_navigate(step)
                if not ok and not self.get_parameter("continue_on_nav_failure").value:
                    self.get_logger().error("Stopping low-level execution after NAVIGATE failure")
                    return
            elif action == GRAB:
                self.execute_grab(step)
            elif action == DROP:
                self.execute_drop(step)
            elif action in UNIMPLEMENTED_ACTIONS:
                self.get_logger().warn(
                    f"Skipping {description}: {action} is not implemented in this MVP"
                )
            else:
                self.get_logger().warn(f"Skipping {description}: unknown action")

    def load_object_simulation_mapping(self):
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

    def load_object_aliases(self):
        aliases = {}
        for symbolic_name in SIMULATED_OBJECTS:
            configured_aliases = list(
                self.get_parameter(
                    f"object_simulation.objects.{symbolic_name}.aliases"
                ).value
            )
            for alias in [symbolic_name, *configured_aliases]:
                normalized = self.clean_manipulation_label(alias)
                if normalized:
                    aliases[normalized] = symbolic_name
        return aliases

    def cache_object_sdf_xml(self):
        world_sdf_path = self.resolve_world_sdf_path(
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
            self.neutralize_model_pose(model_copy)
            model_xml = ET.tostring(model_copy, encoding="unicode")
            self.object_sdf_xml[gazebo_entity] = f'<sdf version="1.6">{model_xml}</sdf>'
            self.get_logger().info(f"Cached SDF XML for {symbolic_name} -> {gazebo_entity}")

    def resolve_world_sdf_path(self, configured_path):
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

    def neutralize_model_pose(self, model):
        for child in list(model):
            if child.tag == "pose":
                child.text = "0 0 0 0 0 0"
                return
        pose = ET.Element("pose")
        pose.text = "0 0 0 0 0 0"
        model.insert(0, pose)

    def execute_grab(self, step):
        target = self.manipulation_target(step)
        normalized = self.normalize_manipulation_target(target)
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
        if not sdf_xml:
            self.get_logger().error(
                f"Cannot GRAB {normalized}: no cached SDF XML for Gazebo entity "
                f"'{gazebo_entity}'. Check object_simulation.world_sdf_path."
            )
            return False
        self.get_logger().info(f"GRAB {normalized}: resolved Gazebo entity '{gazebo_entity}'")
        if not self.delete_with_gz_cli(gazebo_entity):
            return False

        self.held_object = {**object_info, "sdf_xml": sdf_xml}
        self.get_logger().info(f"GRAB {normalized} -> deleted Gazebo entity {gazebo_entity}")
        self.get_logger().info(
            f"Current held object: {self.held_object['symbolic_name']} "
            f"({self.held_object['gazebo_entity']})"
        )
        return True

    def execute_drop(self, step):
        target = self.manipulation_target(step)
        normalized = self.normalize_manipulation_target(target)
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
        drop_pose = self.compute_drop_pose(self.held_object)
        if drop_pose is None:
            return False

        if not self.spawn_with_gz_cli(self.held_object, drop_pose):
            return False

        dropped = self.held_object
        self.held_object = None
        dropped_gazebo_entity = dropped.get(
            "dropped_gazebo_entity",
            dropped["gazebo_entity"],
        )
        self.get_logger().info(
            f"DROP {normalized} -> respawned {dropped_gazebo_entity} at drop pose"
        )
        self.get_logger().info("Current held object: none")
        return True

    def manipulation_target(self, step):
        if step.get("target"):
            return str(step["target"])
        description = str(step.get("description", "")).strip()
        return re.sub(r"^\s*\w+\s+", "", description).strip()

    def normalize_manipulation_target(self, target):
        normalized = self.clean_manipulation_label(target)
        return self.object_aliases.get(normalized, normalized)

    def clean_manipulation_label(self, target):
        normalized = str(target).lower().strip()
        normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^the\s+", "", normalized)
        return re.sub(r"\s+\d+$", "", normalized).strip()

    def gz_model_base_command(self):
        command = [
            str(self.get_parameter("object_simulation.gz_command").value),
            "model",
        ]
        world_name = str(self.get_parameter("object_simulation.gz_world_name").value).strip()
        if world_name:
            command.extend(["-w", world_name])
        return command

    def run_gz_command(self, command, label):
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

    def delete_with_gz_cli(self, gazebo_entity):
        command = self.gz_model_base_command()
        command.extend(["-m", gazebo_entity, "-d"])
        self.get_logger().info(f"Deleting Gazebo model '{gazebo_entity}'")
        return self.run_gz_command(command, "DeleteEntity")

    def hide_with_gz_cli(self, gazebo_entity):
        pose = Pose()
        pose.position.x = float(self.get_parameter("object_simulation.hidden_x").value)
        pose.position.y = float(self.get_parameter("object_simulation.hidden_y").value)
        pose.position.z = float(self.get_parameter("object_simulation.hidden_z").value)
        pose.orientation = self.quaternion_from_yaw(0.0)
        self.get_logger().info(
            f"Hiding Gazebo model '{gazebo_entity}' at "
            f"x={pose.position.x:.3f}, y={pose.position.y:.3f}, z={pose.position.z:.3f}"
        )
        return self.move_with_gz_cli(gazebo_entity, pose, label="HideEntity")

    def set_entity_pose(self, gazebo_entity, pose, label="MoveEntity"):
        return self.move_with_gz_cli(gazebo_entity, pose, label=label)

    def move_with_gz_cli(self, gazebo_entity, pose, label="MoveEntity"):
        yaw = self.yaw_from_quaternion(pose.orientation)
        command = self.gz_model_base_command()
        command.extend(
            [
                "-m",
                gazebo_entity,
                "-x",
                f"{pose.position.x:.6f}",
                "-y",
                f"{pose.position.y:.6f}",
                "-z",
                f"{pose.position.z:.6f}",
                "-R",
                "0.000000",
                "-P",
                "0.000000",
                "-Y",
                f"{yaw:.6f}",
            ]
        )
        self.get_logger().info(
            f"Moving Gazebo model '{gazebo_entity}' to "
            f"x={pose.position.x:.3f}, y={pose.position.y:.3f}, "
            f"z={pose.position.z:.3f}, yaw={yaw:.3f}"
        )
        return self.run_gz_command(command, label)

    def spawn_with_gz_cli(self, held_object, drop_pose):
        yaw = self.yaw_from_quaternion(drop_pose.orientation)
        dropped_name = self.dropped_entity_name(held_object["gazebo_entity"])
        spawn_sdf_xml = self.sdf_xml_with_model_name_and_pose(
            held_object["sdf_xml"],
            dropped_name,
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

            command = self.gz_model_base_command()
            command.extend(
                [
                    "-m",
                    dropped_name,
                    "-f",
                    temp_path,
                    "-x",
                    f"{drop_pose.position.x:.6f}",
                    "-y",
                    f"{drop_pose.position.y:.6f}",
                    "-z",
                    f"{drop_pose.position.z:.6f}",
                    "-Y",
                    f"{yaw:.6f}",
                ]
            )
            self.get_logger().info(
                f"Spawning Gazebo model '{dropped_name}' at "
                f"x={drop_pose.position.x:.3f}, y={drop_pose.position.y:.3f}, "
                f"z={drop_pose.position.z:.3f}, yaw={yaw:.3f}"
            )
            if not self.run_gz_command(command, "SpawnEntity"):
                return False

            held_object["dropped_gazebo_entity"] = dropped_name
            return True
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as exc:
                    self.get_logger().warn(f"Failed to remove temporary SDF {temp_path}: {exc}")

    def dropped_entity_name(self, gazebo_entity):
        return f"{gazebo_entity}_dropped_{int(time.time() * 1000)}"

    def sdf_xml_with_model_name_and_pose(self, sdf_xml, model_name, x, y, z, yaw):
        try:
            root = ET.fromstring(sdf_xml)
        except ET.ParseError as exc:
            self.get_logger().warn(
                f"Failed to update spawn SDF, using cached SDF XML unchanged: {exc}"
            )
            return sdf_xml

        model = root.find("model")
        if model is None:
            self.get_logger().warn("Spawn SDF has no <model>, using cached SDF XML unchanged")
            return sdf_xml

        model.set("name", model_name)
        pose = model.find("pose")
        if pose is None:
            pose = ET.Element("pose")
            model.insert(0, pose)
        pose.text = f"{x:.6f} {y:.6f} {z:.6f} 0 0 {yaw:.6f}"
        return ET.tostring(root, encoding="unicode")

    def compute_drop_pose(self, held_object):
        fixed_pose = self.fixed_drop_pose(held_object)
        if fixed_pose is not None:
            return fixed_pose
        child_table_pose = self.static_child_delivery_drop_pose(held_object)
        if child_table_pose is not None:
            return child_table_pose

        world_frame = str(self.get_parameter("object_simulation.world_frame").value)
        robot_base_frame = str(self.get_parameter("object_simulation.robot_base_frame").value)
        robot_pose = self.current_robot_pose_for_drop(world_frame, robot_base_frame)
        if robot_pose is None:
            self.get_logger().error(
                f"Cannot DROP {held_object['symbolic_name']}: TF pose could not be obtained"
            )
            return None

        yaw = self.yaw_from_quaternion(robot_pose.pose.orientation)
        drop_distance = float(self.get_parameter("object_simulation.drop_distance").value)
        drop_pose = Pose()
        drop_pose.position.x = robot_pose.pose.position.x + drop_distance * math.cos(yaw)
        drop_pose.position.y = robot_pose.pose.position.y + drop_distance * math.sin(yaw)
        z = self.drop_z_for_current_destination(held_object)
        if z is None:
            return None
        drop_pose.position.z = z
        drop_pose.orientation = self.quaternion_from_yaw(yaw)
        self.get_logger().info(
            f"Computed DROP pose for {held_object['symbolic_name']}: "
            f"robot=({robot_pose.pose.position.x:.3f}, {robot_pose.pose.position.y:.3f}, "
            f"yaw={yaw:.3f}), drop=({drop_pose.position.x:.3f}, "
            f"{drop_pose.position.y:.3f}, {drop_pose.position.z:.3f})"
        )
        return drop_pose

    def drop_z_for_current_destination(self, held_object):
        if self.last_navigation_target_is_child:
            drop_pose = self.static_scene_graph.resolve_drop_pose(role="child_delivery_drop")
            if drop_pose is None:
                self.get_logger().error(
                    "Static scene graph has no drop pose with role 'child_delivery_drop'"
                )
                return None
            return float(drop_pose["z"])
        return float(held_object["drop_z"])

    def static_child_delivery_drop_pose(self, held_object):
        if not self.last_navigation_target_is_child:
            return None

        graph_drop_pose = self.static_scene_graph.resolve_drop_pose(
            role="child_delivery_drop",
        )
        if graph_drop_pose is None:
            self.get_logger().error(
                "Static scene graph has no drop pose with role 'child_delivery_drop'"
            )
            return None

        yaw = float(graph_drop_pose["yaw"])
        x = float(graph_drop_pose["x"])
        y = float(graph_drop_pose["y"])
        z = float(graph_drop_pose["z"])

        drop_pose = Pose()
        drop_pose.position.x = x
        drop_pose.position.y = y
        drop_pose.position.z = z
        drop_pose.orientation = self.quaternion_from_yaw(yaw)
        self.get_logger().info(
            f"Using child delivery table DROP pose for {held_object['symbolic_name']}: "
            f"x={drop_pose.position.x:.3f}, y={drop_pose.position.y:.3f}, "
            f"z={drop_pose.position.z:.3f}, yaw={yaw:.3f}; TF lookup skipped"
        )
        return drop_pose

    def fixed_drop_pose(self, held_object):
        if not bool(self.get_parameter("object_simulation.fixed_drop.enabled").value):
            return None

        configured_z = float(self.get_parameter("object_simulation.fixed_drop.z").value)
        z = configured_z if configured_z >= 0.0 else self.drop_z_for_current_destination(held_object)
        if z is None:
            return None
        yaw = float(self.get_parameter("object_simulation.fixed_drop.yaw").value)
        drop_pose = Pose()
        drop_pose.position.x = float(self.get_parameter("object_simulation.fixed_drop.x").value)
        drop_pose.position.y = float(self.get_parameter("object_simulation.fixed_drop.y").value)
        drop_pose.position.z = z
        drop_pose.orientation = self.quaternion_from_yaw(yaw)
        self.get_logger().info(
            f"Using fixed DROP pose for {held_object['symbolic_name']}: "
            f"x={drop_pose.position.x:.3f}, y={drop_pose.position.y:.3f}, "
            f"z={drop_pose.position.z:.3f}, yaw={yaw:.3f}; TF lookup skipped"
        )
        return drop_pose

    def current_robot_pose_for_drop(self, preferred_frame, robot_base_frame):
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
                    f"Using TF frame '{frame}' for DROP pose; assuming it is aligned "
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

    def load_plan(self):
        dump_dir = self.dump_dir()
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

    def dump_dir(self):
        return os.path.join(
            self.get_parameter("output_root").value,
            "empower_output",
            self.get_parameter("use_case").value,
        )

    def wait_for_objects(self):
        timeout_sec = float(self.get_parameter("marker_timeout_sec").value)
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and time.monotonic() < deadline:
            if self._centroids is not None and self._names is not None:
                objects = self.objects_from_markers(self._names, self._centroids)
                if objects:
                    return objects
            rclpy.spin_once(self, timeout_sec=0.1)
        return {}

    def objects_from_markers(self, name_list, centroid_list):
        objects = {}
        map_frame = self.get_parameter("map_frame").value
        for name_marker, centroid_marker in zip(name_list.markers, centroid_list.markers):
            label = name_marker.text.strip()
            if not label:
                continue
            try:
                pose = self.marker_to_pose_stamped(centroid_marker, map_frame)
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

    def execute_navigate(self, step):
        if self.is_child_target_step(step):
            nav_goal = self.child_static_nav_pose()
            if nav_goal is None:
                return False
            for waypoint_goal in self.child_via_waypoint_poses():
                waypoint_goal.header.stamp = self.get_clock().now().to_msg()
                self.get_logger().info(
                    "Navigating to child via static scene graph waypoint "
                    f"({waypoint_goal.pose.position.x:.3f}, "
                    f"{waypoint_goal.pose.position.y:.3f})"
                )
                if not navigate(
                    self,
                    self.nav_client,
                    waypoint_goal,
                    float(self.get_parameter("action_server_timeout_sec").value),
                    float(self.get_parameter("nav_result_timeout_sec").value),
                ):
                    return False
            nav_goal.header.stamp = self.get_clock().now().to_msg()
            self.get_logger().info(
                "Resolving NAVIGATE to child via static semantic pose graph "
                f"({nav_goal.pose.position.x:.3f}, {nav_goal.pose.position.y:.3f})"
            )
            ok = navigate(
                self,
                self.nav_client,
                nav_goal,
                float(self.get_parameter("action_server_timeout_sec").value),
                float(self.get_parameter("nav_result_timeout_sec").value),
            )
            if ok:
                self.last_navigation_target_is_child = True
            return ok

        if self.is_person_target_step(step):
            nav_goal = self.person_static_nav_pose()
            if nav_goal is None:
                return False
            nav_goal.header.stamp = self.get_clock().now().to_msg()
            self.get_logger().info(
                "Resolving NAVIGATE to person/adult via static semantic pose graph "
                f"({nav_goal.pose.position.x:.3f}, {nav_goal.pose.position.y:.3f})"
            )
            ok = navigate(
                self,
                self.nav_client,
                nav_goal,
                float(self.get_parameter("action_server_timeout_sec").value),
                float(self.get_parameter("nav_result_timeout_sec").value),
            )
            if ok:
                self.last_navigation_target_is_child = False
            return ok

        global_label, target_pose = self.resolve_global_target_pose(step)
        if target_pose is not None:
            robot_pose = self.wait_for_current_robot_pose()
            if robot_pose is None:
                self.get_logger().error(
                    "Cannot execute NAVIGATE from global_detections.json: "
                    "robot pose in map frame is unavailable"
                )
                return False
            nav_goal = make_nav_goal_pose(
                target_pose,
                robot_pose,
                float(self.get_parameter("standoff_distance").value),
            )
            nav_goal.header.stamp = self.get_clock().now().to_msg()
            self.get_logger().info(
                f"Executing NAVIGATE to '{global_label}' from global_detections.json "
                f"({target_pose.pose.position.x:.3f}, {target_pose.pose.position.y:.3f})"
            )
            ok = navigate(
                self,
                self.nav_client,
                nav_goal,
                float(self.get_parameter("action_server_timeout_sec").value),
                float(self.get_parameter("nav_result_timeout_sec").value),
            )
            if ok:
                self.last_navigation_target_is_child = False
            return ok

        target_label, marker = self.resolve_target_marker(step)
        if marker is None:
            self.get_logger().error(
                f"Cannot execute NAVIGATE: target '{step.get('target')}' was not found"
            )
            return False

        try:
            target_pose = self.marker_to_pose_stamped(marker, self.get_parameter("map_frame").value)
        except RuntimeError as exc:
            self.get_logger().error(str(exc))
            return False
        robot_pose = self.wait_for_current_robot_pose()
        if robot_pose is None:
            self.get_logger().error(
                "Cannot execute NAVIGATE from EMPOWER centroid: "
                "robot pose in map frame is unavailable"
            )
            return False
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
        ok = navigate(
            self,
            self.nav_client,
            nav_goal,
            float(self.get_parameter("action_server_timeout_sec").value),
            float(self.get_parameter("nav_result_timeout_sec").value),
        )
        if ok:
            self.last_navigation_target_is_child = False
        return ok

    def load_global_detections(self):
        path = os.path.join(self.dump_dir(), "global_detections.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        except (OSError, json.JSONDecodeError) as exc:
            self.get_logger().warn(f"Cannot load global detections from {path}: {exc}")
            return []
        objects = data.get("objects", [])
        if objects:
            self.get_logger().info(
                f"Loaded {len(objects)} global objects from global_detections.json"
            )
        return objects

    def can_resolve_global_navigation_step(self, step):
        return self.resolve_global_target_pose(step, log_ambiguity=False)[1] is not None

    def resolve_global_target_pose(self, step, log_ambiguity=True):
        if not self.global_objects:
            return None, None
        matches = []
        for candidate in self.target_candidates(step):
            normalized = self.normalize_label(candidate)
            for obj in self.global_objects:
                if normalized in self.global_object_names(obj):
                    matches.append(obj)
        deduped = []
        seen = set()
        for obj in matches:
            object_id = obj.get("id", "")
            if object_id and object_id not in seen:
                seen.add(object_id)
                deduped.append(obj)
        if not deduped:
            return None, None
        if len(deduped) > 1:
            if log_ambiguity:
                names = ", ".join(obj.get("id", obj.get("label", "object")) for obj in deduped)
                self.get_logger().error(
                    f"Ambiguous global visual target '{step.get('target')}': matches {names}"
                )
            return None, None
        obj = deduped[0]
        centroid = obj.get("centroid_map")
        if not centroid or len(centroid) < 3:
            return None, None
        pose = PoseStamped()
        pose.header.frame_id = self.get_parameter("map_frame").value
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(centroid[0])
        pose.pose.position.y = float(centroid[1])
        pose.pose.position.z = float(centroid[2])
        pose.pose.orientation.w = 1.0
        return obj.get("id") or obj.get("label"), pose

    def global_object_names(self, obj):
        names = set()
        aliases = obj.get("aliases") or []
        for value in [obj.get("id"), obj.get("label"), *aliases]:
            normalized = self.normalize_label(value)
            if normalized:
                names.add(normalized)
                names.add(re.sub(r"\s+\d+$", "", normalized).strip())
        return names

    def resolve_target_marker(self, step):
        normalized_objects = {
            self.normalize_label(label): (label, marker)
            for label, marker in getattr(self, "_objects", {}).items()
        }
        for candidate in self.target_candidates(step):
            normalized = self.normalize_label(candidate)
            if normalized in normalized_objects:
                return normalized_objects[normalized]
        return None, None

    def is_child_target_step(self, step):
        return any(self.is_child_alias(candidate) for candidate in self.target_candidates(step))

    def is_person_target_step(self, step):
        return any(self.is_person_alias(candidate) for candidate in self.target_candidates(step))

    def is_static_scene_navigation_step(self, step):
        return self.is_child_target_step(step) or self.is_person_target_step(step)

    def is_child_alias(self, label):
        return self.static_scene_graph.is_alias_of(label, "child")

    def is_person_alias(self, label):
        return self.static_scene_graph.is_alias_of(label, "therapist")

    def child_static_nav_pose(self):
        pose = self.static_scene_graph.resolve_nav_pose("child", role="deliver_object")
        if pose is None:
            self.get_logger().error(
                "Static scene graph has no child nav pose with role 'deliver_object'"
            )
            return None
        return self.pose_stamped_from_graph_pose(pose)

    def child_via_waypoint_poses(self):
        waypoints = self.static_scene_graph.resolve_waypoints_for_nav_pose(
            "child",
            role="deliver_object",
        )
        return [self.pose_stamped_from_graph_pose(waypoint) for waypoint in waypoints]

    def person_static_nav_pose(self):
        pose = self.static_scene_graph.resolve_nav_pose(
            "therapist",
            role="receive_instruction",
        )
        if pose is None:
            self.get_logger().error(
                "Static scene graph has no therapist nav pose with role 'receive_instruction'"
            )
            return None
        return self.pose_stamped_from_graph_pose(pose)

    def pose_stamped_from_graph_pose(self, graph_pose):
        pose = PoseStamped()
        pose.header.frame_id = self.static_scene_graph.frame_id
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(graph_pose["x"])
        pose.pose.position.y = float(graph_pose["y"])
        pose.pose.position.z = float(graph_pose.get("z", 0.0))
        pose.pose.orientation = self.quaternion_from_yaw(float(graph_pose["yaw"]))
        return pose

    def target_candidates(self, step):
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
            normalized = self.normalize_label(candidate)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(candidate)
        return deduped

    def normalize_label(self, label):
        label = str(label).lower().strip()
        label = re.sub(r"[^a-z0-9 ]+", " ", label)
        label = re.sub(r"\b(the|a|an)\b", " ", label)
        return re.sub(r"\s+", " ", label).strip()

    def marker_to_pose_stamped(self, marker, target_frame):
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

        x, y, z = self.apply_transform(
            (pose.pose.position.x, pose.pose.position.y, pose.pose.position.z),
            transform,
        )
        pose.header.frame_id = target_frame
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        return pose

    def current_robot_pose(self):
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

    def wait_for_current_robot_pose(self):
        map_frame = self.get_parameter("map_frame").value
        base_frame = self.get_parameter("base_frame").value
        timeout_sec = float(self.get_parameter("tf_timeout_sec").value)
        deadline = time.monotonic() + timeout_sec
        last_exc = None
        warned = False
        while rclpy.ok() and time.monotonic() < deadline:
            try:
                transform = self.tf_buffer.lookup_transform(
                    map_frame,
                    base_frame,
                    Time(),
                    timeout=Duration(seconds=0.0),
                )
            except TransformException as exc:
                last_exc = exc
                if not warned:
                    self.get_logger().warn(
                        f"Waiting for robot pose TF {map_frame}->{base_frame}: {exc}"
                    )
                    warned = True
                rclpy.spin_once(self, timeout_sec=0.1)
                continue
            pose = PoseStamped()
            pose.header.frame_id = map_frame
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = transform.transform.translation.x
            pose.pose.position.y = transform.transform.translation.y
            pose.pose.position.z = transform.transform.translation.z
            pose.pose.orientation = transform.transform.rotation
            self.get_logger().info(
                f"Using current robot pose from TF {map_frame}->{base_frame}: "
                f"x={pose.pose.position.x:.3f}, y={pose.pose.position.y:.3f}"
            )
            return pose
        self.get_logger().error(
            f"Timed out after {timeout_sec:.1f}s waiting for robot pose TF "
            f"{map_frame}->{base_frame}: {last_exc}"
        )
        return None

    def apply_transform(self, point, transform):
        rotated = self.rotate_vector(point, transform.transform.rotation)
        translation = transform.transform.translation
        return (
            rotated[0] + translation.x,
            rotated[1] + translation.y,
            rotated[2] + translation.z,
        )

    def rotate_vector(self, vector, quaternion):
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

    def yaw_from_quaternion(self, quaternion):
        x = quaternion.x
        y = quaternion.y
        z = quaternion.z
        w = quaternion.w
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        return math.atan2(siny_cosp, cosy_cosp)

    def quaternion_from_yaw(self, yaw):
        quaternion = Quaternion()
        quaternion.z = math.sin(yaw / 2.0)
        quaternion.w = math.cos(yaw / 2.0)
        return quaternion

    def format_step(self, step):
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

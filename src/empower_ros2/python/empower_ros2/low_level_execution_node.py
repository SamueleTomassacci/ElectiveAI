import copy
import json
import math
import os
import re
import time

import rclpy
from geometry_msgs.msg import PoseStamped
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
SKIPPED_ACTIONS = {"GRAB", "DROP", "PUSH", "PULL"}


class LowLevelExecutionNode(Node):
    """ROS2 port of EMPOWER low_level_execution.py for NAVIGATE-only execution."""

    def __init__(self):
        super().__init__("empower_low_level_execution")
        self.declare_parameter("use_case", "tiago_task")
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

        self._centroids = None
        self._names = None
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            self.get_parameter("nav_action_name").value,
        )
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

        objects = self._wait_for_objects()
        if not objects:
            self.get_logger().error("No EMPOWER object centroids available")
            return

        self.get_logger().info(
            "Loaded EMPOWER object centroids: " + ", ".join(sorted(objects.keys()))
        )
        for step in plan:
            action = str(step.get("action", "")).upper()
            description = step.get("description") or self._format_step(step)
            if action == NAVIGATE:
                ok = self._execute_navigate(step)
                if not ok and not self.get_parameter("continue_on_nav_failure").value:
                    self.get_logger().error("Stopping low-level execution after NAVIGATE failure")
                    return
            elif action in SKIPPED_ACTIONS:
                self.get_logger().info(f"Skipping {description}: action not implemented in ROS2 port")
            else:
                self.get_logger().warn(f"Skipping {description}: unknown action")

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

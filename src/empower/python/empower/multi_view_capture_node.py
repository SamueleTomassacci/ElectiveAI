import json
import math
import os
import pickle
import time

import cv2
import numpy as np
import open3d as o3d
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import PoseStamped, Quaternion
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener

from empower.pointcloud_utils import depth_image_to_point_cloud
from empower.primitive_actions import navigate


def normalize_angle(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def quaternion_from_yaw(yaw):
    q = Quaternion()
    q.z = math.sin(yaw * 0.5)
    q.w = math.cos(yaw * 0.5)
    return q


def quaternion_to_matrix(q):
    x, y, z, w = q.x, q.y, q.z, q.w
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return np.eye(3)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


class MultiViewCaptureNode(Node):
    """Capture a fixed-position 360-degree RGB-D sweep for EMPOWER."""

    def __init__(self):
        super().__init__("empower_multi_view_capture")
        self.declare_parameter("use_case", "therapy_task")
        self.declare_parameter("output_root", "/root/exchange/output")
        self.declare_parameter("rgb_topic", "/head_front_camera/rgb/image_raw")
        self.declare_parameter("depth_topic", "/head_front_camera/depth/image_raw")
        self.declare_parameter("camera_info_topic", "/head_front_camera/depth/camera_info")
        self.declare_parameter("depth_unit_scale_to_mm", -1.0)
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("camera_frame", "")
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter("tf_timeout_sec", 2.0)
        self.declare_parameter("tf_startup_timeout_sec", 20.0)
        self.declare_parameter("action_server_timeout_sec", 20.0)
        self.declare_parameter("nav_result_timeout_sec", 300.0)
        self.declare_parameter("multi_view.enabled", True)
        self.declare_parameter("multi_view.overlap_ratio", 0.15)
        self.declare_parameter("multi_view.yaw_coverage", 2.0 * math.pi)
        self.declare_parameter("multi_view.min_views", 4)
        self.declare_parameter("multi_view.max_views", 12)
        self.declare_parameter("multi_view.settle_time_sec", 1.0)
        self.declare_parameter("multi_view.rotation_mode", "nav2")
        self.declare_parameter("multi_view.save_debug", True)
        self.declare_parameter("multi_view.output_subdir", "views")

        self.bridge = CvBridge()
        self.rgb_msg = None
        self.depth_msg = None
        self.camera_info_msg = None
        self.rgb_seq = 0
        self.depth_seq = 0
        self.camera_info_seq = 0
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            self.get_parameter("nav_action_name").value,
        )

        self.create_subscription(
            Image,
            self.get_parameter("rgb_topic").value,
            self._rgb_callback,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Image,
            self.get_parameter("depth_topic").value,
            self._depth_callback,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            CameraInfo,
            self.get_parameter("camera_info_topic").value,
            self._camera_info_callback,
            qos_profile_sensor_data,
        )

    def _rgb_callback(self, msg):
        self.rgb_msg = msg
        self.rgb_seq += 1

    def _depth_callback(self, msg):
        self.depth_msg = msg
        self.depth_seq += 1

    def _camera_info_callback(self, msg):
        self.camera_info_msg = msg
        self.camera_info_seq += 1

    def run(self):
        if not bool(self.get_parameter("multi_view.enabled").value):
            self.get_logger().warn(
                "[MultiViewCapture] multi_view.enabled is false; no multi-view capture was run"
            )
            return
        if str(self.get_parameter("multi_view.rotation_mode").value).lower() != "nav2":
            raise ValueError("Only multi_view.rotation_mode=nav2 is supported in this MVP")

        self.get_logger().info("[MultiViewCapture] Waiting for RGB, depth and camera info")
        self._wait_for_messages()
        camera_info = self.camera_info_msg
        hfov, n_views, actual_yaw_step, overlap = self._compute_view_schedule(camera_info)
        self.get_logger().info(
            f"[MultiViewCapture] Camera HFOV: {hfov:.3f} rad / {math.degrees(hfov):.1f} deg"
        )
        self.get_logger().info(f"[MultiViewCapture] Requested overlap: {overlap:.2f}")
        self.get_logger().info(f"[MultiViewCapture] Computed n_views: {n_views}")
        self.get_logger().info(
            f"[MultiViewCapture] Actual yaw step: {math.degrees(actual_yaw_step):.1f} deg"
        )

        initial_pose = self._current_robot_pose()
        initial_yaw = yaw_from_quaternion(initial_pose.pose.orientation)
        dump_dir, views_dir = self._prepare_output_dirs()
        manifest = {
            "hfov_rad": hfov,
            "overlap_ratio_requested": overlap,
            "yaw_coverage_rad": float(self.get_parameter("multi_view.yaw_coverage").value),
            "n_views": n_views,
            "actual_yaw_step_rad": actual_yaw_step,
            "map_frame": self.get_parameter("map_frame").value,
            "base_frame": self.get_parameter("base_frame").value,
            "views": [],
        }

        for view_id in range(n_views):
            relative_yaw = view_id * actual_yaw_step
            target_yaw = normalize_angle(initial_yaw + relative_yaw)
            self.get_logger().info(
                f"[MultiViewCapture] Capturing view {view_id}/{n_views} at yaw "
                f"{target_yaw:.3f} rad ({math.degrees(target_yaw):.1f} deg)"
            )
            if view_id > 0:
                self._rotate_in_place(initial_pose, target_yaw)
            time.sleep(float(self.get_parameter("multi_view.settle_time_sec").value))
            rgb_seq, depth_seq, info_seq = self.rgb_seq, self.depth_seq, self.camera_info_seq
            self._wait_for_fresh_messages(rgb_seq, depth_seq, info_seq)
            view_record = self._save_view(
                view_id,
                relative_yaw,
                target_yaw,
                os.path.join(views_dir, f"view_{view_id:03d}"),
                dump_dir,
                write_legacy=(view_id == 0),
            )
            manifest["views"].append(view_record)

        manifest_path = os.path.join(dump_dir, "views_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as manifest_file:
            json.dump(manifest, manifest_file, indent=2)
        self.get_logger().info(f"[MultiViewCapture] Saved views manifest: {manifest_path}")

    def _wait_for_messages(self):
        while rclpy.ok():
            if self.rgb_msg is not None and self.depth_msg is not None and self.camera_info_msg is not None:
                return
            rclpy.spin_once(self, timeout_sec=0.1)
        raise RuntimeError("ROS shutdown while waiting for camera messages")

    def _wait_for_fresh_messages(self, rgb_seq, depth_seq, info_seq):
        deadline = time.monotonic() + 5.0
        while rclpy.ok() and time.monotonic() < deadline:
            if self.rgb_seq > rgb_seq and self.depth_seq > depth_seq and self.camera_info_seq > info_seq:
                return
            rclpy.spin_once(self, timeout_sec=0.1)
        self.get_logger().warn("[MultiViewCapture] Timed out waiting for fresh camera messages; using latest")

    def _compute_view_schedule(self, camera_info):
        width = float(camera_info.width)
        fx = float(camera_info.k[0])
        if width <= 0.0 or fx <= 0.0:
            raise ValueError(f"Invalid CameraInfo for HFOV calculation: width={width}, fx={fx}")
        overlap = float(self.get_parameter("multi_view.overlap_ratio").value)
        overlap = min(0.5, max(0.0, overlap))
        hfov = 2.0 * math.atan(width / (2.0 * fx))
        desired_yaw_step = hfov * (1.0 - overlap)
        if desired_yaw_step <= 0.0:
            raise ValueError("Computed non-positive desired yaw step")
        coverage = float(self.get_parameter("multi_view.yaw_coverage").value)
        n_views = math.ceil(coverage / desired_yaw_step)
        min_views = int(self.get_parameter("multi_view.min_views").value)
        max_views = int(self.get_parameter("multi_view.max_views").value)
        n_views = max(min_views, min(max_views, n_views))
        actual_yaw_step = coverage / float(n_views)
        return hfov, n_views, actual_yaw_step, overlap

    def _current_robot_pose(self):
        map_frame = self.get_parameter("map_frame").value
        base_frame = self.get_parameter("base_frame").value
        transform = self._lookup_transform_with_retry(map_frame, base_frame)
        pose = PoseStamped()
        pose.header.frame_id = map_frame
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = transform.transform.translation.x
        pose.pose.position.y = transform.transform.translation.y
        pose.pose.position.z = transform.transform.translation.z
        pose.pose.orientation = transform.transform.rotation
        return pose

    def _rotate_in_place(self, initial_pose, target_yaw):
        goal = PoseStamped()
        goal.header.frame_id = initial_pose.header.frame_id
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.pose.position.x = initial_pose.pose.position.x
        goal.pose.position.y = initial_pose.pose.position.y
        goal.pose.position.z = 0.0
        goal.pose.orientation = quaternion_from_yaw(target_yaw)
        ok = navigate(
            self,
            self.nav_client,
            goal,
            float(self.get_parameter("action_server_timeout_sec").value),
            float(self.get_parameter("nav_result_timeout_sec").value),
        )
        if not ok:
            raise RuntimeError(f"Nav2 failed while rotating to yaw {target_yaw:.3f}")

    def _prepare_output_dirs(self):
        use_case = self.get_parameter("use_case").value
        output_root = self.get_parameter("output_root").value
        dump_dir = os.path.join(output_root, "empower_output", use_case)
        views_dir = os.path.join(dump_dir, str(self.get_parameter("multi_view.output_subdir").value))
        os.makedirs(views_dir, exist_ok=True)
        return dump_dir, views_dir

    def _save_view(self, view_id, relative_yaw, target_yaw, view_dir, dump_dir, write_legacy=False):
        os.makedirs(view_dir, exist_ok=True)
        rgb = self.bridge.imgmsg_to_cv2(self.rgb_msg, desired_encoding="bgr8")
        depth_msg = self.depth_msg
        depth = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
        depth = np.asarray(depth)
        depth_is_float = np.issubdtype(depth.dtype, np.floating)
        if depth.ndim == 3:
            depth = depth[:, :, 0]
        depth = depth.astype(np.float32)
        depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
        depth_scale = float(self.get_parameter("depth_unit_scale_to_mm").value)
        if depth_scale < 0:
            depth_scale = 1000.0 if depth_is_float else 1.0
        depth_mm = depth * depth_scale

        rgb_path = os.path.join(view_dir, "rgb.jpg")
        depth_path = os.path.join(view_dir, "depth.npy")
        camera_info_pkl_path = os.path.join(view_dir, "camera_info.pkl")
        camera_info_json_path = os.path.join(view_dir, "camera_info.json")
        pose_path = os.path.join(view_dir, "camera_pose_map.json")

        cv2.imwrite(rgb_path, rgb)
        np.save(depth_path, depth_mm)

        camera_info = self._camera_info_dict(self.camera_info_msg)
        with open(camera_info_pkl_path, "wb") as camera_file:
            pickle.dump(camera_info, camera_file, protocol=2)
        with open(camera_info_json_path, "w", encoding="utf-8") as camera_file:
            json.dump(camera_info, camera_file, indent=2)

        camera_pose = self._lookup_camera_pose(camera_info["frame_id"])
        with open(pose_path, "w", encoding="utf-8") as pose_file:
            json.dump(camera_pose, pose_file, indent=2)

        if write_legacy:
            self._write_legacy_outputs(rgb, depth_mm, camera_info, dump_dir)

        return {
            "view_id": view_id,
            "yaw": relative_yaw,
            "target_yaw_map": target_yaw,
            "rgb_path": rgb_path,
            "depth_path": depth_path,
            "camera_info_path": camera_info_pkl_path,
            "camera_info_json_path": camera_info_json_path,
            "camera_pose_map_path": pose_path,
        }

    def _camera_info_dict(self, msg):
        return {
            "k": list(msg.k),
            "width": msg.width,
            "height": msg.height,
            "frame_id": msg.header.frame_id,
        }

    def _lookup_camera_pose(self, camera_info_frame):
        map_frame = self.get_parameter("map_frame").value
        configured_camera_frame = str(self.get_parameter("camera_frame").value).strip()
        camera_frame = configured_camera_frame or camera_info_frame
        transform = self._lookup_transform_with_retry(map_frame, camera_frame)
        t = transform.transform.translation
        q = transform.transform.rotation
        rotation = quaternion_to_matrix(q)
        matrix = np.eye(4)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = [t.x, t.y, t.z]
        return {
            "parent_frame": map_frame,
            "child_frame": camera_frame,
            "transform_direction": "camera_frame_to_map",
            "translation": [t.x, t.y, t.z],
            "rotation_xyzw": [q.x, q.y, q.z, q.w],
            "matrix": matrix.tolist(),
        }

    def _lookup_transform_with_retry(self, target_frame, source_frame):
        startup_timeout = float(self.get_parameter("tf_startup_timeout_sec").value)
        deadline = time.monotonic() + startup_timeout
        last_exc = None
        first_warning = True
        while rclpy.ok() and time.monotonic() < deadline:
            try:
                return self.tf_buffer.lookup_transform(
                    target_frame,
                    source_frame,
                    Time(),
                    timeout=Duration(seconds=0.0),
                )
            except TransformException as exc:
                last_exc = exc
                if first_warning:
                    self.get_logger().warn(
                        f"[MultiViewCapture] Waiting for TF {target_frame}->{source_frame}: {exc}"
                    )
                    first_warning = False
                rclpy.spin_once(self, timeout_sec=0.1)
        raise RuntimeError(
            f"Cannot read TF {target_frame}->{source_frame} after "
            f"{startup_timeout:.1f}s: {last_exc}"
        )

    def _write_legacy_outputs(self, rgb, depth_mm, camera_info, dump_dir):
        cv2.imwrite(os.path.join(dump_dir, "scan.jpg"), rgb)
        np.save(os.path.join(dump_dir, "depth.npy"), depth_mm)
        with open(os.path.join(dump_dir, "camera_info.pkl"), "wb") as camera_file:
            pickle.dump(camera_info, camera_file, protocol=2)
        with open(os.path.join(dump_dir, "camera_info.json"), "w", encoding="utf-8") as camera_file:
            json.dump(camera_info, camera_file, indent=2)

        k = camera_info["k"]
        camera_intrinsics = np.array(
            [[k[0], 0.0, k[2]], [0.0, k[4], k[5]], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        point_cloud = depth_image_to_point_cloud(depth_mm, camera_intrinsics)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(point_cloud)
        pcd.transform(np.array([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]]))
        o3d.io.write_point_cloud(os.path.join(dump_dir, "depth_pointcloud.pcd"), pcd)


def main(args=None):
    rclpy.init(args=args)
    node = MultiViewCaptureNode()
    try:
        node.run()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

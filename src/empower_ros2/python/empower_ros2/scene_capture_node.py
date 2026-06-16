import json
import os
import pickle

import cv2
import numpy as np
import open3d as o3d
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

from empower_ros2.pointcloud_utils import depth_image_to_point_cloud


class SceneCaptureNode(Node):
    """ROS2 adapter for empower's create_pcl.py."""

    def __init__(self):
        super().__init__("empower_scene_capture")
        self.declare_parameter("use_case", "person_table_child_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("rgb_topic", "/head_front_camera/rgb/image_raw")
        self.declare_parameter("depth_topic", "/head_front_camera/depth/image_raw")
        self.declare_parameter("camera_info_topic", "/head_front_camera/depth/camera_info")
        self.declare_parameter("depth_unit_scale_to_mm", -1.0)
        self.declare_parameter("capture_once", True)

        self.bridge = CvBridge()
        self.rgb_msg = None
        self.depth_msg = None
        self.camera_info_msg = None
        self.captured = False

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
        self.get_logger().info("Waiting for RGB, depth and camera info messages")

    def _rgb_callback(self, msg):
        self.rgb_msg = msg
        self._try_capture()

    def _depth_callback(self, msg):
        self.depth_msg = msg
        self._try_capture()

    def _camera_info_callback(self, msg):
        self.camera_info_msg = msg
        self._try_capture()

    def _try_capture(self):
        if self.captured and self.get_parameter("capture_once").value:
            return
        if self.rgb_msg is None or self.depth_msg is None or self.camera_info_msg is None:
            return

        use_case = self.get_parameter("use_case").value
        output_root = self.get_parameter("output_root").value
        scan_dir = os.path.join(output_root, "images", use_case)
        dump_dir = os.path.join(output_root, "output", use_case)
        os.makedirs(scan_dir, exist_ok=True)
        os.makedirs(dump_dir, exist_ok=True)

        rgb = self.bridge.imgmsg_to_cv2(self.rgb_msg, desired_encoding="bgr8")
        scan_path = os.path.join(scan_dir, "scan.jpg")
        cv2.imwrite(scan_path, rgb)

        depth = self.bridge.imgmsg_to_cv2(self.depth_msg, desired_encoding="passthrough")
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
        np.save(os.path.join(dump_dir, "depth.npy"), depth_mm)

        k = self.camera_info_msg.k
        camera_intrinsics = np.array(
            [[k[0], 0.0, k[2]], [0.0, k[4], k[5]], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        point_cloud = depth_image_to_point_cloud(depth_mm, camera_intrinsics)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(point_cloud)
        pcd.transform(np.array([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]]))
        o3d.io.write_point_cloud(os.path.join(dump_dir, "depth_pointcloud.pcd"), pcd)

        camera_info = {
            "k": list(self.camera_info_msg.k),
            "width": self.camera_info_msg.width,
            "height": self.camera_info_msg.height,
            "frame_id": self.camera_info_msg.header.frame_id,
        }
        with open(os.path.join(dump_dir, "camera_info.pkl"), "wb") as camera_file:
            pickle.dump(camera_info, camera_file, protocol=2)
        with open(os.path.join(dump_dir, "camera_info.json"), "w", encoding="utf-8") as camera_file:
            json.dump(camera_info, camera_file, indent=2)

        self.captured = True
        self.get_logger().info(f"Saved empower scene capture in {dump_dir}")
        if self.get_parameter("capture_once").value:
            rclpy.shutdown()


def main(args=None):
    rclpy.init(args=args)
    node = SceneCaptureNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

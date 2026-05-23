import os
import pickle

import cv2
import numpy as np
import open3d as o3d
import rclpy
from matplotlib.colors import to_rgb
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import PointCloud2

from empower_ros2.pointcloud_utils import open3d_to_pointcloud2


COLORS = ["red", "green", "blue", "magenta", "cyan", "yellow"] * 3


def rgb_to_bgr255(rgb_color):
    r, g, b = rgb_color
    return [int(b * 255), int(g * 255), int(r * 255)]


class ColorPclNode(Node):
    """ROS2 adapter for empower's color_pcl.py."""

    def __init__(self):
        super().__init__("empower_color_pcl")
        self.declare_parameter("use_case", "tiago_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("camera_frame", "head_front_camera_rgb_optical_frame")
        self.declare_parameter("pointcloud_topic", "/empower/colored_pointcloud")
        self.declare_parameter("publish_rate_hz", 1.0)
        self.cloud_msg = None

        qos = QoSProfile(depth=1)
        qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.publisher = self.create_publisher(
            PointCloud2,
            self.get_parameter("pointcloud_topic").value,
            qos,
        )
        self._run()
        period = 1.0 / float(self.get_parameter("publish_rate_hz").value)
        self.create_timer(period, self._publish)

    def _run(self):
        use_case = self.get_parameter("use_case").value
        output_root = self.get_parameter("output_root").value
        scan_dir = os.path.join(output_root, "images", use_case)
        dump_dir = os.path.join(output_root, "output", use_case)

        pcd = o3d.io.read_point_cloud(os.path.join(dump_dir, "depth_pointcloud.pcd"))
        pcd.colors = o3d.utility.Vector3dVector(np.tile(to_rgb("gray"), (len(pcd.points), 1)))

        image = cv2.imread(os.path.join(scan_dir, "scan.jpg"))
        if image is None:
            raise FileNotFoundError(os.path.join(scan_dir, "scan.jpg"))
        height, width, _ = image.shape

        with open(os.path.join(dump_dir, "detection.pkl"), "rb") as detection_file:
            detections = pickle.load(detection_file)
        with open(os.path.join(dump_dir, "camera_info.pkl"), "rb") as camera_file:
            camera_info = pickle.load(camera_file)

        masks = []
        masks_flipped = []
        for key in detections.keys():
            mask = detections[key]["mask"]
            masks.append(mask[:, :, 0])
            masks_flipped.append(cv2.flip(mask[:, :, 0], 1))

        proj_matrix = camera_info["k"]
        fx = proj_matrix[0]
        fy = proj_matrix[4]
        cx = proj_matrix[2]
        cy = proj_matrix[5]

        colors_dict = {}
        for idx, point in enumerate(pcd.points):
            if point[2] < 0:
                x_img = int((fx * point[0] / point[2]) + cx)
                y_img = int((fy * point[1] / point[2]) + cy)
                for color_id, (mask, mask_flipped) in enumerate(zip(masks, masks_flipped)):
                    color = to_rgb(COLORS[color_id])
                    if 0 <= x_img < width and 0 <= y_img < height and mask[y_img, x_img] != 0:
                        image[y_img, x_img] = rgb_to_bgr255(color)
                    if 0 <= x_img < width and 0 <= y_img < height and mask_flipped[y_img, x_img] != 0:
                        colors_dict.setdefault(color_id, [])
                        pcd.colors[idx] = color
                        colors_dict[color_id].append(np.asarray(point))

        cv2.imwrite(os.path.join(dump_dir, "colored_image.png"), image)
        o3d.io.write_point_cloud(os.path.join(dump_dir, "colored_pcl.pcd"), pcd)
        with open(os.path.join(dump_dir, "colors_dict.pkl"), "wb") as colors_file:
            pickle.dump(colors_dict, colors_file, protocol=2)

        self.cloud_msg = open3d_to_pointcloud2(
            pcd,
            self.get_parameter("camera_frame").value,
            self.get_clock().now().to_msg(),
        )
        self.get_logger().info(f"Saved colored empower point cloud in {dump_dir}")

    def _publish(self):
        if self.cloud_msg is None:
            return
        self.cloud_msg.header.stamp = self.get_clock().now().to_msg()
        self.publisher.publish(self.cloud_msg)


def main(args=None):
    rclpy.init(args=args)
    node = ColorPclNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

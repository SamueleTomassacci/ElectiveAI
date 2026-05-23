import copy
import os
import pickle

import numpy as np
import rclpy
from matplotlib.colors import to_rgb
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray


COLORS = ["red", "green", "blue", "magenta", "cyan", "yellow"] * 3


def get_rotation_and_translation(trans):
    translation = trans.transform.translation
    rotation = trans.transform.rotation
    t = np.array([translation.x, translation.y, translation.z])
    qx = rotation.x
    qy = rotation.y
    qz = rotation.z
    qw = rotation.w
    r = 2 * np.array(
        [
            [qw**2 + qx**2 - 0.5, qx * qy - qw * qz, qw * qy + qx * qz],
            [qw * qz + qx * qy, qw**2 + qy**2 - 0.5, qy * qz - qw * qx],
            [qx * qz - qw * qy, qw * qx + qy * qz, qw**2 + qz**2 - 0.5],
        ]
    )
    return r, t


class MarkerCentroidsNode(Node):
    """ROS2 adapter for empower's spawn_clusters_points.py marker logic."""

    def __init__(self):
        super().__init__("empower_marker_centroids")
        self.declare_parameter("use_case", "tiago_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("fixed_frame", "map")
        self.declare_parameter("camera_frame", "head_front_camera_rgb_optical_frame")
        self.declare_parameter("publish_rate_hz", 10.0)

        self.centroid_pub = self.create_publisher(MarkerArray, "/pcl_centroids", 10)
        self.maximum_pub = self.create_publisher(MarkerArray, "/pcl_maximum", 10)
        self.names_pub = self.create_publisher(MarkerArray, "/pcl_names", 10)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.marker_arrays = None
        period = 1.0 / float(self.get_parameter("publish_rate_hz").value)
        self.create_timer(period, self._publish)
        self.create_timer(1.0, self._load_once)

    def _load_once(self):
        if self.marker_arrays is not None:
            return
        use_case = self.get_parameter("use_case").value
        dump_dir = os.path.join(self.get_parameter("output_root").value, "output", use_case)
        required_files = ["detection.pkl", "colors_dict.pkl"]
        for required_file in required_files:
            if not os.path.exists(os.path.join(dump_dir, required_file)):
                self.get_logger().info(f"Waiting for {required_file} in {dump_dir}")
                return
        with open(os.path.join(dump_dir, "detection.pkl"), "rb") as detection_file:
            detections = pickle.load(detection_file)
        with open(os.path.join(dump_dir, "colors_dict.pkl"), "rb") as colors_file:
            colors_dict = pickle.load(colors_file)

        fixed_frame = self.get_parameter("fixed_frame").value
        camera_frame = self.get_parameter("camera_frame").value
        try:
            transform = self.tf_buffer.lookup_transform(
                camera_frame,
                fixed_frame,
                Time(),
                timeout=Duration(seconds=2.0),
            )
            rotation, translation = get_rotation_and_translation(transform)
            frame_id = fixed_frame
        except TransformException as exc:
            self.get_logger().warn(f"TF lookup failed, publishing markers in {camera_frame}: {exc}")
            rotation = None
            translation = None
            frame_id = camera_frame

        centroids = MarkerArray()
        maximums = MarkerArray()
        names = MarkerArray()

        marker_id = 0
        for color_id, points in colors_dict.items():
            if len(points) == 0:
                continue
            centroid = np.mean(points, axis=0)
            filtered = [point for point in points if np.linalg.norm(point - centroid) < 100]
            if not filtered:
                filtered = points
            new_centroid = np.mean(filtered, axis=0)
            max_point = np.max(filtered, axis=0)
            label = detections.get(color_id, {}).get("label", f"object_{color_id}")

            centroid_marker = self._set_marker(new_centroid, COLORS[color_id], marker_id, frame_id, rotation, translation)
            centroids.markers.append(centroid_marker)
            maximums.markers.append(self._set_marker(max_point, COLORS[color_id], marker_id, frame_id, rotation, translation))
            names.markers.append(self._set_name(centroid_marker, label))
            marker_id += 1

        self.marker_arrays = (centroids, maximums, names)
        self.get_logger().info("Loaded empower centroid markers")

    def _transform_point(self, point, rotation, translation):
        point = np.asarray(point) / 1000.0
        optical_transform = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]])
        point = np.dot(optical_transform, point)
        if rotation is None:
            return point
        return np.dot(np.transpose(rotation), point - translation)

    def _set_marker(self, point, color_name, marker_id, frame_id, rotation, translation):
        point = self._transform_point(point, rotation, translation)
        marker = Marker()
        marker.header.frame_id = frame_id
        marker.header.stamp = self.get_clock().now().to_msg()
        marker.ns = "empower_centroids"
        marker.id = marker_id
        marker.type = Marker.SPHERE
        marker.action = Marker.ADD
        marker.pose.position.x = float(point[0])
        marker.pose.position.y = float(point[1])
        marker.pose.position.z = float(point[2])
        marker.pose.orientation.w = 1.0
        color = to_rgb(color_name)
        marker.color.r = color[0]
        marker.color.g = color[1]
        marker.color.b = color[2]
        marker.color.a = 1.0
        marker.scale.x = 0.05
        marker.scale.y = 0.05
        marker.scale.z = 0.05
        return marker

    def _set_name(self, marker, label):
        text_marker = copy.deepcopy(marker)
        text_marker.ns = "empower_names"
        text_marker.type = Marker.TEXT_VIEW_FACING
        text_marker.pose.position.z += 0.1
        text_marker.text = str(label)
        text_marker.color.r = 1.0
        text_marker.color.g = 1.0
        text_marker.color.b = 1.0
        text_marker.color.a = 1.0
        text_marker.scale.z = 0.1
        return text_marker

    def _publish(self):
        if self.marker_arrays is None:
            return
        for marker_array in self.marker_arrays:
            for marker in marker_array.markers:
                marker.header.stamp = self.get_clock().now().to_msg()
        centroids, maximums, names = self.marker_arrays
        self.centroid_pub.publish(centroids)
        self.maximum_pub.publish(maximums)
        self.names_pub.publish(names)


def main(args=None):
    rclpy.init(args=args)
    node = MarkerCentroidsNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

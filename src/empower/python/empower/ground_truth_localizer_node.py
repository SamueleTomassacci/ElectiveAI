import math

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer,TransformBroadcaster, TransformException, TransformListener


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w *q.z + q.x * q.y)
    cosy_cosp=1.0 - 2.0 *(q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp,cosy_cosp)


def quaternion_from_yaw(yaw):
    half_yaw = yaw * 0.5
    return (0.0,0.0,math.sin(half_yaw), math.cos(half_yaw))


class GroundTruthLocalizerNode(Node):
    

    def __init__(self):

        super().__init__("ground_truth_localizer")

        self.declare_parameter("ground_truth_topic", "/ground_truth_odom")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("odom_frame","odom")
        self.declare_parameter("base_frame","base_footprint")
        self.declare_parameter("publish_rate_hz", 30.0)
        self.declare_parameter("transform_timeout_sec",0.05)

        self.latest_ground_truth = None
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.tf_broadcaster = TransformBroadcaster(self)

        self.create_subscription(
            Odometry,
            self.get_parameter("ground_truth_topic").value,
            self.ground_truth_callback,
            10,
        )
        period =1.0/float(self.get_parameter("publish_rate_hz").value)
        self.create_timer(period,self.publish_transform)
        self.get_logger().info(
            "Using Gazebo ground truth as localization source for map->odom"
        )

    def ground_truth_callback(self, msg):
        self.latest_ground_truth = msg

    def publish_transform(self):
        if self.latest_ground_truth is None:
            return

        odom_frame = self.get_parameter("odom_frame").value
        base_frame = self.get_parameter("base_frame").value
        timeout = float(self.get_parameter("transform_timeout_sec").value)
        try:
            odom_to_base = self.tf_buffer.lookup_transform(
                odom_frame,
                base_frame,
                Time(),
                timeout=Duration(seconds=timeout),
            )
        except TransformException as exc:
            self.get_logger().warn(
                f"Waiting for TF {odom_frame}->{base_frame}: {exc}",
                throttle_duration_sec=2.0,
            )
            return

        gt_pose= self.latest_ground_truth.pose.pose

        map_base_x = gt_pose.position.x
        map_base_y = gt_pose.position.y

        map_base_yaw = yaw_from_quaternion(gt_pose.orientation)

        odom_base_translation = odom_to_base.transform.translation
        odom_base_x = odom_base_translation.x
        odom_base_y = odom_base_translation.y
        odom_base_yaw = yaw_from_quaternion(odom_to_base.transform.rotation)

        map_odom_yaw = map_base_yaw - odom_base_yaw
        cos_yaw= math.cos(map_odom_yaw)
        sin_yaw= math.sin(map_odom_yaw)
        rotated_odom_base_x =cos_yaw * odom_base_x - sin_yaw*odom_base_y
        rotated_odom_base_y = sin_yaw*odom_base_x + cos_yaw * odom_base_y

        map_odom_x = map_base_x - rotated_odom_base_x
        map_odom_y = map_base_y-rotated_odom_base_y

        transform = TransformStamped()
        transform.header.stamp = self.latest_ground_truth.header.stamp
        transform.header.frame_id = self.get_parameter("map_frame").value
        transform.child_frame_id = odom_frame
        transform.transform.translation.x = map_odom_x
        transform.transform.translation.y = map_odom_y
        transform.transform.translation.z = 0.0
        qx, qy, qz,qw = quaternion_from_yaw(map_odom_yaw)
        transform.transform.rotation.x = qx
        transform.transform.rotation.y = qy
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw

        self.tf_broadcaster.sendTransform(transform)


def main(args=None):
    rclpy.init(args=args)
    node =GroundTruthLocalizerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

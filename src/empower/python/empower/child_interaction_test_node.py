import json
import math

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String


class ChildInteractionTestNode(Node):
    """Small driver for testing only the robot-child interaction phase."""

    def __init__(self):
        super().__init__("child_interaction_test_node")
        self.declare_parameters(
            namespace="",
            parameters=[
                ("navigate_to_child", True),
                ("start_activity_on_nav_failure", False),
                ("start_delay_sec", 2.0),
                ("child_pose", [0.0, 0.95, 1.5708]),
                ("map_frame", "map"),
                ("nav_action_name", "navigate_to_pose"),
                ("action_server_timeout_sec", 20.0),
                ("task_description", "Child-only interaction test."),
                ("activity_description", "Ask the child how they feel."),
                ("activity_type", "mood_check_teddy"),
                ("child_name", "Luca"),
            ],
        )

        self.dialog_command_pub = self.create_publisher(
            String,
            "/hri/dialog_command",
            10,
        )
        self.feedback_pub = self.create_publisher(String, "/mission/feedback", 10)
        self.child_ready_pub = self.create_publisher(String, "/mission/child_ready", 10)
        self.create_subscription(
            String,
            "/hri/dialog_complete",
            self.dialog_complete_callback,
            10,
        )

        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            str(self.get_parameter("nav_action_name").value),
        )
        self._started = False
        self._timer = self.create_timer(
            float(self.get_parameter("start_delay_sec").value),
            self.start,
        )

    def start(self):
        if self._started:
            return
        self._started = True
        self._timer.cancel()

        if bool(self.get_parameter("navigate_to_child").value):
            self.navigate_to_child()
        else:
            self.publish_feedback("Navigation disabled; starting child activity directly")
            self.start_child_activity()

    def navigate_to_child(self):
        timeout_sec = float(self.get_parameter("action_server_timeout_sec").value)
        action_name = str(self.get_parameter("nav_action_name").value)
        self.publish_feedback(f"Waiting for Nav2 action server '{action_name}'")
        if not self.nav_client.wait_for_server(timeout_sec=timeout_sec):
            self.publish_feedback(f"Nav2 action server '{action_name}' is not available")
            self.maybe_start_after_nav_failure()
            return

        pose_values = list(self.get_parameter("child_pose").value)
        goal = NavigateToPose.Goal()
        goal.pose = self.make_pose_stamped(pose_values)
        self.publish_feedback(
            "Sending child pose goal: "
            f"x={pose_values[0]:.3f}, y={pose_values[1]:.3f}, yaw={pose_values[2]:.3f}"
        )
        send_future = self.nav_client.send_goal_async(goal)
        send_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            self.publish_feedback("Nav2 rejected the child pose goal")
            self.maybe_start_after_nav_failure()
            return
        self.publish_feedback("Nav2 accepted the child pose goal")
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.goal_result_callback)

    def goal_result_callback(self, future):
        result = future.result()
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self.publish_feedback("TIAGo reached the child pose")
            self.start_child_activity()
            return
        self.publish_feedback(f"Navigation to child failed with status {result.status}")
        self.maybe_start_after_nav_failure()

    def maybe_start_after_nav_failure(self):
        if bool(self.get_parameter("start_activity_on_nav_failure").value):
            self.publish_feedback("Starting child activity despite navigation failure")
            self.start_child_activity()

    def start_child_activity(self):
        context = {
            "child_name": str(self.get_parameter("child_name").value),
            "task": str(self.get_parameter("task_description").value),
            "activity_description": str(
                self.get_parameter("activity_description").value
            ),
            "activity_type": str(self.get_parameter("activity_type").value),
        }
        payload = {"command": "start_child_activity", "context": context}
        message = String()
        message.data = json.dumps(payload)
        self.dialog_command_pub.publish(message)
        self.publish_child_ready()
        self.publish_feedback(f"Started child activity: {message.data}")

    def make_pose_stamped(self, pose_values):
        pose = PoseStamped()
        pose.header.frame_id = str(self.get_parameter("map_frame").value)
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(pose_values[0])
        pose.pose.position.y = float(pose_values[1])
        pose.pose.position.z = 0.0
        yaw = float(pose_values[2])
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def dialog_complete_callback(self, msg):
        self.publish_feedback(f"Child activity complete: {msg.data}")

    def publish_child_ready(self):
        message = String()
        message.data = "child_ready"
        self.child_ready_pub.publish(message)

    def publish_feedback(self, text):
        message = String()
        message.data = text
        self.feedback_pub.publish(message)
        self.get_logger().info(text)


def main(args=None):
    rclpy.init(args=args)
    node = ChildInteractionTestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

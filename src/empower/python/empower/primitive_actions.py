import math

import rclpy
from action_msgs.msg import GoalStatus
from nav2_msgs.action import NavigateToPose


def quaternion_from_yaw(yaw):
    half_yaw = yaw * 0.5
    return (0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw))


def make_nav_goal_pose(target_pose, robot_pose, standoff_distance):

    target_x = target_pose.pose.position.x
    target_y = target_pose.pose.position.y
    robot_x = robot_pose.pose.position.x if robot_pose is not None else target_x - 1.0
    robot_y = robot_pose.pose.position.y if robot_pose is not None else target_y - 1.0

    away_x = robot_x - target_x
    away_y = robot_y - target_y
    distance = math.hypot(away_x, away_y)
    if distance < 1.0e-6:
        away_x = -1.0
        away_y = -1.0
        distance = math.sqrt(2.0)

    goal_x = target_x + standoff_distance * away_x / distance
    goal_y = target_y + standoff_distance * away_y / distance
    yaw = math.atan2(target_y - goal_y, target_x - goal_x)
    qx, qy, qz, qw = quaternion_from_yaw(yaw)

    goal_pose = type(target_pose)()
    goal_pose.header = target_pose.header
    goal_pose.pose.position.x = float(goal_x)
    goal_pose.pose.position.y = float(goal_y)
    goal_pose.pose.position.z = 0.0
    goal_pose.pose.orientation.x = qx
    goal_pose.pose.orientation.y = qy
    goal_pose.pose.orientation.z = qz
    goal_pose.pose.orientation.w = qw
    return goal_pose


def navigate(node, action_client, goal_pose, action_server_timeout_sec, result_timeout_sec):
    
    if not action_client.wait_for_server(timeout_sec=action_server_timeout_sec):
        action_name = getattr(action_client, "_action_name", "navigate_to_pose")
        node.get_logger().error(
            f"Nav2 action server '{action_name}' is not available"
        )
        return False

    goal = NavigateToPose.Goal()
    goal.pose = goal_pose

    node.get_logger().info(
        "Sending NAVIGATE goal to Nav2: "
        f"frame={goal_pose.header.frame_id} "
        f"x={goal_pose.pose.position.x:.3f} "
        f"y={goal_pose.pose.position.y:.3f}"
    )
    send_future = action_client.send_goal_async(goal)
    rclpy.spin_until_future_complete(
        node,
        send_future,
        timeout_sec=action_server_timeout_sec,
    )
    if not send_future.done():
        node.get_logger().error("Timed out while sending NAVIGATE goal to Nav2")
        return False

    goal_handle = send_future.result()
    if goal_handle is None or not goal_handle.accepted:
        node.get_logger().error("Nav2 rejected NAVIGATE goal")
        return False

    result_future = goal_handle.get_result_async()
    rclpy.spin_until_future_complete(node, result_future, timeout_sec=result_timeout_sec)
    if not result_future.done():
        node.get_logger().error("Timed out waiting for NAVIGATE result")
        goal_handle.cancel_goal_async()
        return False

    result = result_future.result()
    if result.status == GoalStatus.STATUS_SUCCEEDED:
        node.get_logger().info("NAVIGATE succeeded")
        return True

    status_name = _goal_status_name(result.status)
    node.get_logger().error(f"NAVIGATE failed with status {status_name}")
    return False


def _goal_status_name(status):
    names = {
        GoalStatus.STATUS_UNKNOWN: "UNKNOWN",
        GoalStatus.STATUS_ACCEPTED: "ACCEPTED",
        GoalStatus.STATUS_EXECUTING: "EXECUTING",
        GoalStatus.STATUS_CANCELING: "CANCELING",
        GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
        GoalStatus.STATUS_CANCELED: "CANCELED",
        GoalStatus.STATUS_ABORTED: "ABORTED",
    }
    return names.get(status, str(status))

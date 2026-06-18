#!/usr/bin/env python3
import argparse
import math
import sys


def quaternion_from_yaw(yaw):
    from geometry_msgs.msg import Quaternion

    quaternion = Quaternion()
    quaternion.z = math.sin(yaw / 2.0)
    quaternion.w = math.cos(yaw / 2.0)
    return quaternion


def wait_for_first_service(node, client_factories, timeout_sec):
    deadline = node.get_clock().now().nanoseconds / 1e9 + timeout_sec
    for name, client in client_factories:
        remaining = max(0.0, deadline - node.get_clock().now().nanoseconds / 1e9)
        if client.wait_for_service(timeout_sec=remaining):
            return name, client
    return None, None


def call_service(node, client, request, timeout_sec):
    import rclpy

    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)
    if not future.done():
        raise TimeoutError(
            f"service call timed out after {timeout_sec:.1f}s. "
            "In this Gazebo launch the entity_state ROS services may be advertised "
            "but not answering; use the gz model command and visual check instead."
        )
    return future.result()


def print_pose(label, pose):
    print(
        f"{label}: x={pose.position.x:.6f}, y={pose.position.y:.6f}, "
        f"z={pose.position.z:.6f}, qz={pose.orientation.z:.6f}, qw={pose.orientation.w:.6f}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--entity",
        default="clear_red_book_on_right_table",
        help="Gazebo entity/model name",
    )
    parser.add_argument(
        "--set",
        nargs=4,
        type=float,
        metavar=("X", "Y", "Z", "YAW"),
        help="Set entity pose before reading it back",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    import rclpy
    from gazebo_msgs.msg import EntityState
    from gazebo_msgs.srv import GetEntityState, SetEntityState

    rclpy.init()
    node = rclpy.create_node("gazebo_object_state_debug")
    try:
        get_name, get_client = wait_for_first_service(
            node,
            [
                ("/get_entity_state", node.create_client(GetEntityState, "/get_entity_state")),
                (
                    "/gazebo/get_entity_state",
                    node.create_client(GetEntityState, "/gazebo/get_entity_state"),
                ),
            ],
            args.timeout,
        )
        if get_client is None:
            print("ERROR: no GetEntityState service found", file=sys.stderr)
            return 2
        print(f"Using get service: {get_name}")

        set_client = None
        set_name = None
        if args.set is not None:
            set_name, set_client = wait_for_first_service(
                node,
                [
                    (
                        "/set_entity_state",
                        node.create_client(SetEntityState, "/set_entity_state"),
                    ),
                    (
                        "/gazebo/set_entity_state",
                        node.create_client(SetEntityState, "/gazebo/set_entity_state"),
                    ),
                ],
                args.timeout,
            )
            if set_client is None:
                print("ERROR: no SetEntityState service found", file=sys.stderr)
                return 2
            print(f"Using set service: {set_name}")

        get_request = GetEntityState.Request()
        get_request.name = args.entity
        get_request.reference_frame = "world"
        get_response = call_service(node, get_client, get_request, args.timeout)
        print(f"Initial get success={get_response.success}, status='{get_response.status_message}'")
        print_pose("Initial pose", get_response.state.pose)

        if args.set is not None:
            x, y, z, yaw = args.set
            state = EntityState()
            state.name = args.entity
            state.pose.position.x = x
            state.pose.position.y = y
            state.pose.position.z = z
            state.pose.orientation = quaternion_from_yaw(yaw)
            state.reference_frame = "world"

            set_request = SetEntityState.Request()
            set_request.state = state
            set_response = call_service(node, set_client, set_request, args.timeout)
            print(f"Set success={set_response.success}, status='{set_response.status_message}'")

            get_response = call_service(node, get_client, get_request, args.timeout)
            print(f"After get success={get_response.success}, status='{get_response.status_message}'")
            print_pose("After pose", get_response.state.pose)
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

import json
import os
import socket
import time

import rclpy
from rclpy.node import Node

from empower_ros2.plan_parser import write_plan_json
from empower_ros2.report_writer import write_readable_reports


class PlanGeneratorNode(Node):
    """ROS2 adapter for empower's execute_task.py."""

    def __init__(self):
        super().__init__("empower_plan_generator")
        self.declare_parameter("use_case", "person_table_child_task")
        self.declare_parameter("task_description", "")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("server_host", "127.0.0.1")
        self.declare_parameter("server_port", 10000)
        self.declare_parameter("timeout_sec", 900.0)
        self._run()

    def _run(self):
        use_case = self.get_parameter("use_case").value
        dump_dir = os.path.join(self.get_parameter("output_root").value, "output", use_case)
        os.makedirs(dump_dir, exist_ok=True)
        planning_txt = os.path.join(dump_dir, "planning.txt")
        planning_json = os.path.join(dump_dir, "planning.json")
        if os.path.exists(planning_txt):
            os.remove(planning_txt)

        payload = {
            "command": "detection",
            "task_description": self.get_parameter("task_description").value,
        }
        timeout_sec = float(self.get_parameter("timeout_sec").value)
        deadline = time.time() + timeout_sec
        self._send_trigger(payload, deadline)

        while time.time() < deadline:
            if os.path.exists(planning_txt):
                with open(planning_txt, "r", encoding="utf-8") as plan_file:
                    plan_text = plan_file.read()
                plan = write_plan_json(plan_text, planning_json)
                report = write_readable_reports(dump_dir)
                self.get_logger().info(f"Saved symbolic plan text to {planning_txt}")
                self.get_logger().info(f"Saved parsed symbolic plan JSON to {planning_json}")
                self.get_logger().info(f"Saved readable EMPOWER reports: {', '.join(report['files'])}")
                self.get_logger().info(json.dumps(plan, indent=2))
                rclpy.shutdown()
                return
            time.sleep(0.5)
        raise TimeoutError(f"Timed out waiting for {planning_txt}")

    def _send_trigger(self, payload, deadline):
        host = self.get_parameter("server_host").value
        port = int(self.get_parameter("server_port").value)
        while time.time() < deadline:
            self.get_logger().info("Triggering empower detection/planning through the model cacher")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                    client_socket.settimeout(5.0)
                    client_socket.connect((host, port))
                    client_socket.sendall(json.dumps(payload).encode())
                    response_timeout = max(1.0, deadline - time.time())
                    client_socket.settimeout(response_timeout)
                    response = client_socket.recv(1024).decode()
                    if not response.startswith("ok"):
                        raise RuntimeError(response)
                return
            except (ConnectionRefusedError, socket.timeout, OSError) as exc:
                self.get_logger().warn(
                    f"Model cacher not ready at {host}:{port}: {exc}; retrying",
                    throttle_duration_sec=5.0,
                )
                time.sleep(1.0)
        raise TimeoutError(f"Timed out connecting to model cacher at {host}:{port}")


def main(args=None):
    rclpy.init(args=args)
    PlanGeneratorNode()


if __name__ == "__main__":
    main()

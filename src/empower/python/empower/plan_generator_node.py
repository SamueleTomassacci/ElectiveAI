import json
import os
import re
import socket
import time

import rclpy
from rclpy.node import Node

from empower.plan_parser import write_plan_json
from empower.report_writer import write_readable_reports


class PlanGeneratorNode(Node):

    def __init__(self):
        super().__init__("empower_plan_generator")
        self.declare_parameter("use_case", "therapy_task")
        self.declare_parameter("task_description", "")
        self.declare_parameter("output_root", "/root/exchange/output")
        self.declare_parameter("server_host", "127.0.0.1")
        self.declare_parameter("server_port", 10000)
        self.declare_parameter("timeout_sec", 900.0)
        self.declare_parameter("child_context.enabled", False)
        self.declare_parameter("child_context.aliases", ["child", "the child", "kid"])
        self._run()

    def _run(self):
        use_case = self.get_parameter("use_case").value
        dump_dir = os.path.join(
            self.get_parameter("output_root").value,
            "empower_output",
            use_case,
        )
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
                self._validate_child_navigation(plan)
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

    def _validate_child_navigation(self, plan):
        task_description = str(self.get_parameter("task_description").value)
        if not bool(self.get_parameter("child_context.enabled").value):
            return
        if not self._text_mentions_child(task_description):
            return
        has_child_navigation = any(
            str(step.get("action", "")).upper() == "NAVIGATE"
            and self._text_is_child_alias(str(step.get("target", "")))
            for step in plan
        )
        if has_child_navigation:
            self.get_logger().info("Validated generated plan contains NAVIGATE to child")
            return
        self.get_logger().warn(
            "Generated plan does not contain NAVIGATE to child even though the task mentions "
            "the child; the plan was not modified"
        )

    def _text_mentions_child(self, text):
        normalized_text = self._normalize_child_text(text)
        for alias in self.get_parameter("child_context.aliases").value:
            normalized_alias = self._normalize_child_text(alias)
            if re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_text):
                return True
        return False

    def _text_is_child_alias(self, text):
        normalized = self._normalize_child_text(text)
        return normalized in {
            self._normalize_child_text(alias)
            for alias in self.get_parameter("child_context.aliases").value
        }

    def _normalize_child_text(self, text):
        text = str(text).lower().strip()
        text = re.sub(r"[^a-z0-9 ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return re.sub(r"^the\s+", "", text)


def main(args=None):
    rclpy.init(args=args)
    PlanGeneratorNode()


if __name__ == "__main__":
    main()

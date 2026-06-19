"""ROS 2 node that monitors child gaze attention during HRI child activities."""

import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class AttentionMonitorNode(Node):

    def __init__(self):
        super().__init__("attention_monitor")
        self.declare_parameter("attention_loss_threshold_sec", 2.5)
        self.declare_parameter("attention_cooldown_sec", 20.0)
        self.declare_parameter(
            "activity_target_map.storytelling",
            ["book", "robot"],
        )
        self.declare_parameter(
            "activity_target_map.mood_check_teddy",
            ["teddy bear", "robot"],
        )
        self.declare_parameter(
            "activity_target_map.computer_emotion_recognition",
            ["laptop", "computer"],
        )
        self.declare_parameter(
            "activity_target_map.generic_child_activity",
            ["robot"],
        )

        self._threshold_sec = float(
            self.get_parameter("attention_loss_threshold_sec").value
        )
        self._cooldown_sec = float(
            self.get_parameter("attention_cooldown_sec").value
        )
        self._activity_target_map = self._load_activity_target_map()

        self.alert_pub = self.create_publisher(String, "/hri/attention_alert", 10)

        self.create_subscription(String, "/gaze/status", self._gaze_callback, 10)
        self.create_subscription(String, "/hri/dialog_state", self._dialog_state_callback, 10)
        self.create_subscription(String, "/hri/dialog_command", self._dialog_command_callback, 10)

        self._activity_type = ""
        self._valid_targets: list[str] = []
        self._armed = False
        self._loss_start: float | None = None
        self._cooldown_until = 0.0
        self._last_gaze_status = ""
        self._last_gaze_target: str | None = None
        self._half_threshold_logged = False
        self._gaze_msg_count = 0

        self.get_logger().info(
            f"Attention monitor ready "
            f"(threshold={self._threshold_sec}s, cooldown={self._cooldown_sec}s)"
        )

    def _load_activity_target_map(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for activity in (
            "storytelling",
            "mood_check_teddy",
            "computer_emotion_recognition",
            "generic_child_activity",
        ):
            param_name = f"activity_target_map.{activity}"
            mapping[activity] = list(self.get_parameter(param_name).value)
        return mapping

    def _dialog_command_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        command = payload.get("command", "")
        if command == "start_child_activity":
            context = payload.get("context", {})
            self._activity_type = str(context.get("activity_type", "generic_child_activity"))
            self._valid_targets = self._activity_target_map.get(
                self._activity_type,
                self._activity_target_map.get("generic_child_activity", ["robot"]),
            )
            self._reset_tracking()
            self.get_logger().info(
                f"Activity started: {self._activity_type}, "
                f"valid targets: {self._valid_targets}"
            )
        elif command == "stop":
            self._disarm()

    def _dialog_state_callback(self, msg: String) -> None:
        state = msg.data.strip()
        if state == "speaking":
            if self._activity_type and not self._armed:
                self._armed = True
                self._half_threshold_logged = False
                self.get_logger().debug(f"Armed: dialog_state={state}")
        else:
            if self._armed:
                self._armed = False
                self._loss_start = None
                self._half_threshold_logged = False
                self.get_logger().debug(f"Disarmed: dialog_state={state}")

    def _gaze_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self._last_gaze_status = payload.get("status", "")
        self._last_gaze_target = payload.get("target_class")
        self._gaze_msg_count += 1

        if self._gaze_msg_count <= 3 or self._gaze_msg_count % 100 == 0:
            self.get_logger().debug(
                f"Gaze #{self._gaze_msg_count}: status={self._last_gaze_status}, "
                f"target={self._last_gaze_target}, armed={self._armed}"
            )

        if not self._armed:
            return
        if self._last_gaze_status == "NO_FRAME":
            return

        now = time.monotonic()
        looking_ok = (
            self._last_gaze_status == "LOOKING_AT_OBJECT"
            and self._last_gaze_target is not None
            and self._last_gaze_target.lower() in [t.lower() for t in self._valid_targets]
        )

        if looking_ok:
            self._loss_start = None
            self._half_threshold_logged = False
            return

        if self._loss_start is None:
            self._loss_start = now
            return

        elapsed = now - self._loss_start

        if not self._half_threshold_logged and elapsed >= self._threshold_sec / 2.0:
            self.get_logger().info(
                f"Attention loss building: {elapsed:.1f}s "
                f"(threshold={self._threshold_sec}s), "
                f"gaze={self._last_gaze_status}"
            )
            self._half_threshold_logged = True

        if elapsed >= self._threshold_sec and now >= self._cooldown_until:
            self._publish_alert(elapsed)
            self._cooldown_until = now + self._cooldown_sec
            self._loss_start = None
            self._half_threshold_logged = False

    def _publish_alert(self, elapsed: float) -> None:
        expected = self._valid_targets[0] if self._valid_targets else "robot"
        alert = {
            "reason": self._alert_reason(),
            "elapsed_sec": round(elapsed, 2),
            "activity_type": self._activity_type,
            "expected_target": expected,
        }
        msg = String()
        msg.data = json.dumps(alert)
        self.alert_pub.publish(msg)
        self.get_logger().info(f"Attention alert: {alert}")

    def _alert_reason(self) -> str:
        if self._last_gaze_status == "NO_FACE":
            return "no_face"
        if self._last_gaze_status == "GAZE_OUT_OF_FRAME":
            return "out_of_frame"
        return "looking_elsewhere"

    def _reset_tracking(self) -> None:
        self._loss_start = None
        self._cooldown_until = 0.0
        self._armed = False

    def _disarm(self) -> None:
        self._activity_type = ""
        self._valid_targets = []
        self._reset_tracking()
        self.get_logger().info("Attention monitor disarmed")


def main(args=None):
    rclpy.init(args=args)
    node = AttentionMonitorNode()
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



import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class AttentionMonitorNode(Node):

    def __init__(self):
        super().__init__("attention_monitor")
        self.declare_parameter("attention_loss_threshold_sec", 5.0)
        self.declare_parameter("attention_cooldown_sec", 20.0)
        self.declare_parameter("emotion_smoothing_window_sec", 7.0)
        self.declare_parameter("combined_recall_score_enabled", True)
        self.declare_parameter("recall_attention_weight", 0.7)
        self.declare_parameter("recall_emotion_weight", 0.3)
        self.declare_parameter("recall_score_threshold", 0.8)
        self.declare_parameter("emotion_recall_score.happy", 0.0)
        self.declare_parameter("emotion_recall_score.surprise", 0.1)
        self.declare_parameter("emotion_recall_score.neutral", 0.2)
        self.declare_parameter("emotion_recall_score.unknown",0.3)
        self.declare_parameter("emotion_recall_score.disgust",0.5)
        self.declare_parameter("emotion_recall_score.sad", 0.5)
        self.declare_parameter("emotion_recall_score.angry",0.6)
        self.declare_parameter("emotion_recall_score.fear",0.6)
        self.declare_parameter(
            "activity_target_map.storytelling",
            ["book","robot"],
        )
        self.declare_parameter(
            "activity_target_map.mood_check_teddy",
            ["teddy bear","robot"],
        )
        self.declare_parameter(
            "activity_target_map.computer_emotion_recognition",
            ["laptop", "computer"],
        )
        self.declare_parameter(
            "activity_target_map.generic_child_activity",
            ["robot"],
        )

        self.threshold_sec = float(
            self.get_parameter("attention_loss_threshold_sec").value
        )
        self.cooldown_sec = float(
            self.get_parameter("attention_cooldown_sec").value
        )
        self.activity_target_map = self.load_activity_target_map()

        self.emotion_window_sec = float(
            self.get_parameter("emotion_smoothing_window_sec").value
        )
        self.combined_recall_score_enabled = bool(
            self.get_parameter("combined_recall_score_enabled").value
        )
        self.recall_attention_weight = float(
            self.get_parameter("recall_attention_weight").value
        )
        self.recall_emotion_weight = float(
            self.get_parameter("recall_emotion_weight").value
        )
        self.recall_score_threshold = float(
            self.get_parameter("recall_score_threshold").value
        )
        self.emotion_recall_scores = self.load_emotion_recall_scores()

        self.alert_pub = self.create_publisher(String,"/hri/attention_alert",10)

        self.emotion_smoothed_pub = self.create_publisher(
            String, "/child/emotion_status_smoothed",10,
        )

        self.create_subscription(String,"/gaze/status", self.gaze_callback,10)
        self.create_subscription(String,"/hri/dialog_state", self.dialog_state_callback,10)
        self.create_subscription(String, "/hri/dialog_command", self.dialog_command_callback,10)

        self.create_subscription(String, "/child/emotion_status",self.emotion_callback, 10)

        self.activity_type = ""
        self.valid_targets = []
        self.armed = False
        self.loss_start = None
        self.cooldown_until = 0.0
        self.last_gaze_status = ""
        self.last_gaze_target = None
        self.half_threshold_logged = False

        self.gaze_msg_count = 0

        self.emotion_window = []
        self.last_smoothed_emotion = ""

        self.get_logger().info(
            f"Attention monitor ready "
            f"(threshold={self.threshold_sec}s, cooldown={self.cooldown_sec}s, "
            f"emotion_window={self.emotion_window_sec}s, "
            f"combined_recall_score={self.combined_recall_score_enabled})"
        )

    def load_activity_target_map(self):

        mapping = {}
        for activity in (
            "storytelling",
            "mood_check_teddy",
            "computer_emotion_recognition",
            "generic_child_activity",
        ):
            param_name = f"activity_target_map.{activity}"
            mapping[activity] = list(self.get_parameter(param_name).value)
        return mapping

    def load_emotion_recall_scores(self):

        scores = {}
        for emotion in (
            "happy",
            "surprise",
            "neutral",
            "unknown",
            "disgust",
            "sad",
            "angry",
            "fear",
        ):
            param_name = f"emotion_recall_score.{emotion}"
            scores[emotion] = float(self.get_parameter(param_name).value)
        return scores

    def dialog_command_callback(self,msg):

        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        command = payload.get("command","")
        if command == "start_child_activity":
            context = payload.get("context",{})

            self.activity_type = str(context.get("activity_type","generic_child_activity"))
            self.valid_targets = self.activity_target_map.get(
                self.activity_type,
                self.activity_target_map.get("generic_child_activity",["robot"]),
            )

            self.reset_tracking()
            self.get_logger().info(
                f"Activity started: {self.activity_type}, "
                f"valid targets: {self.valid_targets}"
            )
        elif command == "stop":
            self.disarm()

    def dialog_state_callback(self, msg):
        state = msg.data.strip()
        if state == "speaking":

            if self.activity_type and not self.armed:
                self.armed = True
                self.half_threshold_logged = False

                self.get_logger().info(f"Armed: dialog_state={state}")
        else:

            if self.armed:
                self.armed = False

                self.loss_start = None

                self.half_threshold_logged = False
                self.get_logger().info(f"Disarmed: dialog_state={state}")

    def gaze_callback(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return

        self.last_gaze_status = payload.get("status","")
        self.last_gaze_target = payload.get("target_class")
        self.gaze_msg_count += 1

        if self.gaze_msg_count <= 3:
            self.get_logger().info(
                f"Gaze #{self.gaze_msg_count}: status={self.last_gaze_status}, "
                f"target={self.last_gaze_target}, armed={self.armed}"
            )
        elif self.gaze_msg_count % 200 == 0:
            self.get_logger().info(
                f"Gaze #{self.gaze_msg_count}: status={self.last_gaze_status}, "
                f"target={self.last_gaze_target}, armed={self.armed}"
            )

        if not self.armed:
            return
        if self.last_gaze_status == "NO_FRAME":
            return

        now = time.monotonic()
        looking_ok = (
            self.last_gaze_status == "LOOKING_AT_OBJECT"
            and self.last_gaze_target is not None
            and self.last_gaze_target.lower() in [t.lower() for t in self.valid_targets]
        )

        if looking_ok:
            self.loss_start = None
            self.half_threshold_logged = False
            return

        if self.loss_start is None:
            self.loss_start = now
            return

        elapsed = now - self.loss_start

        recall_score = None
        attention_component = None
        emotion_component = None
        emotion = self.current_smoothed_emotion()
        if self.combined_recall_score_enabled:

            recall_score, attention_component,emotion_component = self.compute_recall_score(
                elapsed,
                emotion,
            )
            alert_ready = recall_score >= self.recall_score_threshold
            progress_ready = recall_score >= self.recall_score_threshold / 2.0
        else:
            alert_ready = elapsed >= self.threshold_sec
            progress_ready= elapsed >= self.threshold_sec/2.0

        if not self.half_threshold_logged and progress_ready:
            if self.combined_recall_score_enabled:
                self.get_logger().info(
                    f"Recall score building: score={recall_score:.2f} "
                    f"(threshold={self.recall_score_threshold:.2f}), "
                    f"elapsed={elapsed:.1f}s, emotion={emotion}, "
                    f"gaze={self.last_gaze_status}"
                )
            else:
                self.get_logger().info(
                    f"Attention loss building: {elapsed:.1f}s "
                    f"(threshold={self.threshold_sec}s), "
                    f"gaze={self.last_gaze_status}"
                )
            self.half_threshold_logged = True

        if alert_ready and now >= self.cooldown_until:
            self.publish_alert(
                elapsed,
                emotion=emotion,
                recall_score=recall_score,
                attention_component=attention_component,
                emotion_component=emotion_component,
            )
            self.cooldown_until = now+self.cooldown_sec
            self.loss_start = None
            self.half_threshold_logged = False

    def current_smoothed_emotion(self):
        return self.last_smoothed_emotion or "unknown"

    def compute_recall_score(self, elapsed,emotion):
        attention_component = elapsed/max(0.001, self.threshold_sec)
        emotion_component = self.emotion_recall_scores.get(
            emotion,
            self.emotion_recall_scores.get("unknown", 0.3),
        )
        recall_score = (
            self.recall_attention_weight*attention_component
            + self.recall_emotion_weight * emotion_component
        )
        return recall_score, attention_component,emotion_component

    def publish_alert(
        self,
        elapsed,
        emotion="unknown",
        recall_score=None,
        attention_component=None,
        emotion_component=None,
    ):
        expected =self.valid_targets[0] if self.valid_targets else "robot"

        alert = {
            "reason": self.alert_reason(),
            "elapsed_sec": round(elapsed, 2),
            "activity_type": self.activity_type,
            "expected_target": expected,
            "emotion": emotion,
        }
        if recall_score is not None:
            alert.update({
                "recall_score": round(recall_score,3),
                "recall_score_threshold": round(self.recall_score_threshold,3),
                "attention_component": round(attention_component,3),
                "emotion_component": round(emotion_component, 3),
                "attention_weight": round(self.recall_attention_weight,3),
                "emotion_weight": round(self.recall_emotion_weight,3),
            })
        msg=String()

        msg.data = json.dumps(alert)
        self.alert_pub.publish(msg)
        self.get_logger().info(f"Attention alert: {alert}")

    def alert_reason(self):
        if self.last_gaze_status == "NO_FACE":
            return "no_face"
        if self.last_gaze_status == "GAZE_OUT_OF_FRAME":
            return "out_of_frame"
        return "looking_elsewhere"

    def emotion_callback(self, msg):
        if not self.activity_type:
            return
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        emotion = payload.get("emotion", "")
        confidence = float(payload.get("confidence", 0.0))
        ts = float(payload.get("timestamp", time.time()))
        self.emotion_window.append((ts, emotion,confidence))
        self.compute_smoothed_emotion()

    def compute_smoothed_emotion(self):
        now = time.time()
        self.emotion_window = [
            entry for entry in self.emotion_window
            if now-entry[0] <= self.emotion_window_sec
        ]

        valid = [
            (ts,em, conf) for ts,em,conf in self.emotion_window
            if em not in ("no_face", "unavailable")
        ]

        if not valid:
            smoothed = "unknown"
        else:
            scores = {}
            for _, em, conf in valid:
                scores[em] = scores.get(em,0.0)+ conf
            smoothed = max(scores, key=scores.get)

        if smoothed != self.last_smoothed_emotion:
            self.get_logger().info(
                f"Smoothed emotion changed: "
                f"{self.last_smoothed_emotion or '(none)'} -> {smoothed}"
            )
            self.last_smoothed_emotion = smoothed

        msg = String()
        msg.data = json.dumps({
            "emotion": smoothed,
            "window_sec": self.emotion_window_sec,
            "sample_count": len(valid),
            "timestamp": now,
        })

        self.emotion_smoothed_pub.publish(msg)

    def reset_tracking(self):
        self.loss_start = None
        self.cooldown_until = 0.0
        self.armed = False

    def disarm(self):
        self.activity_type = ""
        self.valid_targets = []
        self.reset_tracking()

        self.get_logger().info("Attention monitor disarmed")


def main(args=None):
    rclpy.init(args=args)
    node =AttentionMonitorNode()
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

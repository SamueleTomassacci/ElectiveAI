from __future__ import annotations

import json
import time

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from gaze_estimation.config_loader import parse_target_classes, resolve_checkpoint
from gaze_estimation.device import select_device
from gaze_estimation.gazelle_wrapper import GazelleEstimator
from gaze_estimation.geometry import is_gaze_on_object, select_largest_face
from gaze_estimation.visualization import draw_debug_overlay
from gaze_estimation.yolo_world_wrapper import YoloWorldDetector

_EMOTION_CLASS_MAP = {
    "Anger": "angry",
    "Contempt": "neutral",
    "Disgust": "disgust",
    "Fear": "fear",
    "Happiness": "happy",
    "Neutral": "neutral",
    "Sadness": "sad",
    "Surprise": "surprise",
}


class GazeWebcamNode(Node):
    def __init__(self):
        super().__init__("gaze_estimation")
        self._declare_parameters()
        self.params = self._read_parameters()
        self.device = select_device(self.params["device"], warn=self.get_logger().warning)
        self.last_status = None
        self._last_heartbeat = 0.0

        self.gaze_status_pub = self.create_publisher(String, "/gaze/status", 10)
        self.node_status_pub = self.create_publisher(String, "/gaze/node_status", 10)
        self.emotion_pub = self.create_publisher(String, "/child/emotion_status", 10)
        self.create_subscription(
            String, "/hri/dialog_command", self._dialog_command_callback, 10,
        )
        self._initial_target_classes = list(self.params["target_classes"])
        self._activity_detection_map = self._load_activity_detection_map()

        if self.params["print_debug"]:
            self.get_logger().info(f"Selected device: {self.device}")
            self.get_logger().info(f"Target classes: {', '.join(self.params['target_classes'])}")

        self.gazelle = None
        self.yolo = None
        self.capture = None
        self.emotion_recognizer = None
        self._emotion_frame_count = 0

    def _declare_parameters(self):
        self.declare_parameter("camera_index", 0)
        self.declare_parameter("device", "auto")
        self.declare_parameter("target_classes", ["book"])
        self.declare_parameter("show_window", True)
        self.declare_parameter("window_name", "Gaze Estimation Webcam")
        self.declare_parameter("resize_width", 640)
        self.declare_parameter("process_every_n_frames", 2)
        self.declare_parameter("yolo_confidence", 0.10)
        self.declare_parameter("gaze_inout_threshold", 0.50)
        self.declare_parameter("bbox_margin_px", 30)
        self.declare_parameter("use_heatmap_ratio_check", True)
        self.declare_parameter("heatmap_inside_bbox_threshold", 0.15)
        self.declare_parameter("weights_dir", "/root/exchange/models/gaze_estimation")
        self.declare_parameter("gazelle_checkpoint", "gazelle_dinov2_vitl14_inout.pt")
        self.declare_parameter("yolo_world_checkpoint", "yolov8x-worldv2.pt")
        self.declare_parameter("face_detector_checkpoint", "best.pt")
        self.declare_parameter("print_debug", True)
        self.declare_parameter("emotion_process_every_n_frames", 1)
        self.declare_parameter("emotion_model_name", "enet_b0_8_best_vgaf")
        self.declare_parameter(
            "activity_detection_classes.storytelling", ["book"],
        )
        self.declare_parameter(
            "activity_detection_classes.mood_check_teddy", ["teddy bear"],
        )
        self.declare_parameter(
            "activity_detection_classes.computer_emotion_recognition",
            ["laptop", "computer"],
        )

    def _read_parameters(self):
        return {
            "camera_index": int(self.get_parameter("camera_index").value),
            "device": str(self.get_parameter("device").value),
            "target_classes": parse_target_classes(self.get_parameter("target_classes").value),
            "show_window": bool(self.get_parameter("show_window").value),
            "window_name": str(self.get_parameter("window_name").value),
            "resize_width": int(self.get_parameter("resize_width").value),
            "process_every_n_frames": max(1, int(self.get_parameter("process_every_n_frames").value)),
            "yolo_confidence": float(self.get_parameter("yolo_confidence").value),
            "gaze_inout_threshold": float(self.get_parameter("gaze_inout_threshold").value),
            "bbox_margin_px": int(self.get_parameter("bbox_margin_px").value),
            "use_heatmap_ratio_check": bool(self.get_parameter("use_heatmap_ratio_check").value),
            "heatmap_inside_bbox_threshold": float(
                self.get_parameter("heatmap_inside_bbox_threshold").value
            ),
            "weights_dir": str(self.get_parameter("weights_dir").value),
            "gazelle_checkpoint": str(self.get_parameter("gazelle_checkpoint").value),
            "yolo_world_checkpoint": str(self.get_parameter("yolo_world_checkpoint").value),
            "face_detector_checkpoint": str(self.get_parameter("face_detector_checkpoint").value),
            "print_debug": bool(self.get_parameter("print_debug").value),
            "emotion_process_every_n_frames": max(1, int(
                self.get_parameter("emotion_process_every_n_frames").value
            )),
            "emotion_model_name": str(self.get_parameter("emotion_model_name").value),
        }

    def _load_activity_detection_map(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for activity in ("storytelling", "mood_check_teddy", "computer_emotion_recognition"):
            param_name = f"activity_detection_classes.{activity}"
            try:
                value = self.get_parameter(param_name).value
            except rclpy.exceptions.ParameterUninitializedException:
                continue
            if value:
                mapping[activity] = list(value)
        return mapping

    def _dialog_command_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        command = payload.get("command", "")
        if command == "start_child_activity":
            context = payload.get("context", {})
            activity_type = str(context.get("activity_type", ""))
            new_classes = self._activity_detection_map.get(activity_type)
            if new_classes and self.yolo is not None:
                self.yolo.set_classes(new_classes)
                self.get_logger().info(
                    f"Updated YOLO-World classes for activity '{activity_type}': "
                    f"{', '.join(new_classes)}"
                )
            elif self.yolo is not None:
                self.yolo.set_classes(self._initial_target_classes)
                self.get_logger().info(
                    f"No specific detection classes for activity '{activity_type}'; "
                    f"using initial classes: {', '.join(self._initial_target_classes)}"
                )
        elif command == "stop" and self.yolo is not None:
            self.yolo.set_classes(self._initial_target_classes)
            self.get_logger().info(
                f"Activity stopped; restored initial classes: "
                f"{', '.join(self._initial_target_classes)}"
            )

    def _publish_node_status(self, state: str, message: str = "") -> None:
        payload: dict = {"state": state}
        if message:
            payload["message"] = message
        msg = String()
        msg.data = json.dumps(payload)
        self.node_status_pub.publish(msg)

    def _publish_gaze_status(self, status: str, target_class: str | None = None) -> None:
        msg = String()
        msg.data = json.dumps({
            "status": status,
            "target_class": target_class,
            "timestamp": time.time(),
        })
        self.gaze_status_pub.publish(msg)
        now = time.monotonic()
        if now - self._last_heartbeat >= 2.0:
            self._publish_node_status("READY")
            self._last_heartbeat = now

    def initialize(self) -> bool:
        try:
            gazelle_checkpoint = resolve_checkpoint(
                self.params["weights_dir"], self.params["gazelle_checkpoint"], "Gazelle"
            )
            yolo_checkpoint = resolve_checkpoint(
                self.params["weights_dir"], self.params["yolo_world_checkpoint"], "YOLO-World"
            )
            face_checkpoint = resolve_checkpoint(
                self.params["weights_dir"], self.params["face_detector_checkpoint"], "face detector"
            )
        except FileNotFoundError as exc:
            self.get_logger().error(str(exc))
            self._publish_node_status("ERROR", str(exc))
            return False

        try:
            self.gazelle = GazelleEstimator(
                gazelle_checkpoint_path=gazelle_checkpoint,
                face_detector_checkpoint_path=face_checkpoint,
                device=self.device,
                gaze_inout_threshold=self.params["gaze_inout_threshold"],
            )
            self.yolo = YoloWorldDetector(
                checkpoint_path=yolo_checkpoint,
                target_classes=self.params["target_classes"],
                confidence=self.params["yolo_confidence"],
                device=self.device,
            )
        except Exception as exc:
            self.get_logger().error(f"Failed to initialize models: {exc}")
            self._publish_node_status("ERROR", f"Model init failed: {exc}")
            return False

        try:
            from emotiefflib.facial_analysis import EmotiEffLibRecognizer
            self.emotion_recognizer = EmotiEffLibRecognizer(
                engine="torch",
                model_name=self.params["emotion_model_name"],
                device=str(self.device),
            )
            self.get_logger().info(
                f"EmotiEffLib emotion model '{self.params['emotion_model_name']}' "
                f"loaded on {self.device}"
            )
        except Exception as exc:
            self.get_logger().error(
                f"EmotiEffLib failed to load (emotion recognition disabled): {exc}"
            )
            self.emotion_recognizer = None

        self.capture = cv2.VideoCapture(self.params["camera_index"])
        if not self.capture.isOpened():
            error_msg = f"Could not open webcam camera_index={self.params['camera_index']}"
            self.get_logger().error(error_msg)
            self._publish_node_status("ERROR", error_msg)
            return False
        self._publish_node_status("READY")
        self._last_heartbeat = time.monotonic()
        return True

    def run(self):
        if not self.initialize():
            return

        frame_count = 0
        overlay = None
        try:
            while rclpy.ok():
                ok, frame_bgr = self.capture.read()
                if not ok or frame_bgr is None:
                    self._set_status("NO_FRAME")
                    rclpy.spin_once(self, timeout_sec=0.01)
                    continue

                frame_bgr = self._resize_frame(frame_bgr)
                frame_count += 1
                if frame_count % self.params["process_every_n_frames"] == 0:
                    overlay = self._process_frame(frame_bgr)

                display_frame = overlay if overlay is not None else frame_bgr
                if self.params["show_window"]:
                    cv2.imshow(self.params["window_name"], display_frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        self.get_logger().info("Quit requested with q.")
                        break
                rclpy.spin_once(self, timeout_sec=0.001)
        finally:
            self.shutdown()

    def _resize_frame(self, frame_bgr):
        resize_width = self.params["resize_width"]
        if resize_width <= 0 or frame_bgr.shape[1] == resize_width:
            return frame_bgr
        scale = resize_width / float(frame_bgr.shape[1])
        resize_height = int(round(frame_bgr.shape[0] * scale))
        return cv2.resize(frame_bgr, (resize_width, resize_height), interpolation=cv2.INTER_AREA)

    def _process_frame(self, frame_bgr):
        rgb_image = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        gaze_results = self.gazelle.estimate(rgb_image)
        object_detections = self.yolo.predict(frame_bgr)

        gaze_result = None
        if gaze_results:
            face_index = select_largest_face(gaze_results)
            if face_index is not None:
                gaze_result = gaze_results[face_index]

        self._process_emotion(rgb_image, gaze_result)

        if gaze_result is None:
            self._set_status("NO_FACE")
            return draw_debug_overlay(frame_bgr, object_detections=object_detections, status="NO_FACE")

        inout_score = gaze_result.get("inout_score")
        if inout_score is not None and inout_score < self.params["gaze_inout_threshold"]:
            self._set_status("GAZE_OUT_OF_FRAME")
            return draw_debug_overlay(
                frame_bgr,
                gaze_result=gaze_result,
                object_detections=object_detections,
                status="GAZE_OUT_OF_FRAME",
            )

        if not object_detections:
            self._set_status("NO_TARGET_OBJECT")
            return draw_debug_overlay(
                frame_bgr,
                gaze_result=gaze_result,
                object_detections=object_detections,
                status="NO_TARGET_OBJECT",
            )

        matched_object = None
        for detection in object_detections:
            if is_gaze_on_object(
                gaze_point=gaze_result.get("gaze_point"),
                heatmap=gaze_result.get("heatmap"),
                object_bbox=detection["bbox"],
                image_shape=frame_bgr.shape,
                margin_px=self.params["bbox_margin_px"],
                use_heatmap_ratio_check=self.params["use_heatmap_ratio_check"],
                heatmap_inside_bbox_threshold=self.params["heatmap_inside_bbox_threshold"],
            ):
                matched_object = detection
                break

        if matched_object is not None:
            status = f"LOOKING_AT_OBJECT: {matched_object['class_name']}"
            self._set_status("LOOKING_AT_OBJECT", matched_object["class_name"])
        else:
            status = "LOOKING_ELSEWHERE"
            self._set_status("LOOKING_ELSEWHERE")
        return draw_debug_overlay(
            frame_bgr,
            gaze_result=gaze_result,
            object_detections=object_detections,
            status=status,
            matched_object=matched_object,
        )

    def _process_emotion(self, rgb_image, gaze_result):
        self._emotion_frame_count += 1
        if self._emotion_frame_count % self.params["emotion_process_every_n_frames"] != 0:
            return

        if self.emotion_recognizer is None:
            self._publish_emotion_status("unavailable", error="EmotiEffLib not loaded")
            return

        if gaze_result is None:
            self._publish_emotion_status("no_face")
            return

        try:
            x1, y1, x2, y2 = (int(v) for v in gaze_result["face_bbox_px"])
            h, w = rgb_image.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            face_crop = rgb_image[y1:y2, x1:x2]
            if face_crop.size == 0:
                self._publish_emotion_status("no_face")
                return

            _, scores = self.emotion_recognizer.predict_emotions(face_crop, logits=False)
            score_values = scores[0]
            all_scores: dict[str, float] = {}
            for idx, class_name in self.emotion_recognizer.idx_to_emotion_class.items():
                mapped = _EMOTION_CLASS_MAP.get(class_name, class_name.lower())
                all_scores[mapped] = all_scores.get(mapped, 0.0) + float(score_values[idx])

            emotion = max(all_scores, key=all_scores.get)
            self._publish_emotion_status(
                emotion, confidence=all_scores[emotion], all_scores=all_scores,
            )
        except Exception as exc:
            self.get_logger().warning(f"Emotion inference error: {exc}")
            self._publish_emotion_status("unavailable", error=str(exc))

    def _publish_emotion_status(
        self,
        emotion: str,
        confidence: float = 0.0,
        all_scores: dict | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict = {"emotion": emotion, "timestamp": time.time()}
        if error is not None:
            payload["error"] = error
        else:
            payload["confidence"] = round(confidence, 4)
            if all_scores is not None:
                payload["all_scores"] = {k: round(v, 4) for k, v in all_scores.items()}
        msg = String()
        msg.data = json.dumps(payload)
        self.emotion_pub.publish(msg)

    def _set_status(self, status: str, target_class: str | None = None):
        if self.params["print_debug"] and status != self.last_status:
            self.get_logger().info(status)
        self.last_status = status
        self._publish_gaze_status(status, target_class)

    def shutdown(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.params["show_window"]:
            cv2.destroyAllWindows()


def main(args=None):
    rclpy.init(args=args)
    node = GazeWebcamNode()
    try:
        node.run()
    except KeyboardInterrupt:
        node.get_logger().info("Interrupted by Ctrl+C.")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

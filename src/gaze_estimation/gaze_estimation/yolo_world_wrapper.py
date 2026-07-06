
from gaze_estimation.device import yolo_device_arg


class YoloWorldDetector:
    def __init__(self, checkpoint_path, target_classes, confidence, device):
        try:
            from ultralytics import YOLOWorld
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependency 'ultralytics'. Install it before running gaze_webcam."
            ) from exc

        self.confidence = float(confidence)
        self.device = device
        self.model = YOLOWorld(checkpoint_path)
        self.target_classes = []
        self.set_classes(target_classes)

    def set_classes(self, target_classes):
        self.target_classes = list(target_classes or [])
        if self.target_classes:
            self.model.set_classes(self.target_classes)

    def predict(self, image_bgr):
        results = self.model.predict(
            image_bgr,
            verbose=False,
            conf=self.confidence,
            device=yolo_device_arg(self.device),
        )
        detections = []
        for result in results:
            boxes = result.boxes.cpu().numpy()
            for box in boxes:
                confidence = float(box.conf[0])
                if confidence < self.confidence:
                    continue
                class_name = str(result.names[int(box.cls[0])])
                if self.target_classes and class_name not in self.target_classes:
                    continue
                detections.append(
                    {
                        "bbox": [float(v) for v in box.xyxy[0]],
                        "class_name": class_name,
                        "confidence": confidence,
                    }
                )
        return detections

from __future__ import annotations

import numpy as np


class GazelleEstimator:
    def __init__(
        self,
        gazelle_checkpoint_path: str,
        face_detector_checkpoint_path: str,
        device: str,
        gaze_inout_threshold: float = 0.5,
    ):
        try:
            import torch
            from ultralytics import YOLO
            from gaze_estimation.gazelle.model import get_gazelle_model
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependencies for Gazelle. Install torch, torchvision, timm, "
                "pillow, numpy, opencv-python, and ultralytics before running gaze_webcam."
            ) from exc

        self.torch = torch
        self.device = device
        self.gaze_inout_threshold = float(gaze_inout_threshold)
        self.model, self.transform = get_gazelle_model("gazelle_dinov2_vitl14_inout")

        try:
            state_dict = torch.load(gazelle_checkpoint_path, map_location=device, weights_only=True)
        except TypeError:
            state_dict = torch.load(gazelle_checkpoint_path, map_location=device)
        self.model.load_gazelle_state_dict(state_dict)
        self.model.eval()
        self.model.to(self.device)

        self.face_detector = YOLO(face_detector_checkpoint_path)

    def detect_faces(self, rgb_image):
        height, width = rgb_image.shape[:2]
        results = self.face_detector.predict(rgb_image, verbose=False, device=0 if self.device == "cuda" else "cpu")
        detections = []
        for result in results:
            boxes = result.boxes.cpu().numpy()
            for box in boxes:
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                detections.append(
                    {
                        "bbox_px": [x1, y1, x2, y2],
                        "bbox_norm": [
                            x1 / float(width),
                            y1 / float(height),
                            x2 / float(width),
                            y2 / float(height),
                        ],
                        "confidence": float(box.conf[0]),
                    }
                )
        detections.sort(key=lambda item: self._area(item["bbox_px"]), reverse=True)
        return detections

    def estimate(self, rgb_image):
        faces = self.detect_faces(rgb_image)
        if not faces:
            return []

        normalized_bboxes = [[face["bbox_norm"] for face in faces]]
        image_tensor = self.transform(rgb_image).unsqueeze(0).to(self.device)
        model_input = {
            "images": image_tensor,
            "bboxes": normalized_bboxes,
        }

        with self.torch.no_grad():
            output = self.model(model_input)

        heatmaps = output["heatmap"][0]
        inout_scores = output["inout"][0] if output.get("inout") is not None else None

        results = []
        image_height, image_width = rgb_image.shape[:2]
        for index, face in enumerate(faces):
            heatmap = heatmaps[index].detach().cpu().numpy()
            inout_score = None
            if inout_scores is not None:
                inout_score = float(inout_scores[index].detach().cpu().item())

            max_index = np.unravel_index(np.argmax(heatmap), heatmap.shape)
            gaze_x = float(max_index[1] / heatmap.shape[1] * image_width)
            gaze_y = float(max_index[0] / heatmap.shape[0] * image_height)

            results.append(
                {
                    "face_bbox": face["bbox_norm"],
                    "face_bbox_px": face["bbox_px"],
                    "gaze_point": [gaze_x, gaze_y],
                    "heatmap": heatmap,
                    "inout_score": inout_score,
                }
            )
        return results

    @staticmethod
    def _area(bbox):
        x1, y1, x2, y2 = bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

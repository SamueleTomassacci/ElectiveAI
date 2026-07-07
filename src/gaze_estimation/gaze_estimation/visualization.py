
import cv2
import numpy as np


def as_int_point(point):
    return int(round(point[0])), int(round(point[1]))


def overlay_heatmap(image_bgr, heatmap, alpha=0.35):
    if heatmap is None:
        return image_bgr
    heatmap_np = np.asarray(heatmap, dtype=np.float32)
    if heatmap_np.size == 0:
        return image_bgr
    normalized = heatmap_np - float(heatmap_np.min())
    max_value = float(normalized.max())
    if max_value > 0.0:
        normalized = normalized / max_value
    resized = cv2.resize(normalized, (image_bgr.shape[1], image_bgr.shape[0]))
    colored = cv2.applyColorMap((resized * 255).astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.addWeighted(image_bgr, 1.0 - alpha, colored, alpha, 0)


def emotion_overlay_text(emotion_status):
    if not isinstance(emotion_status, dict):
        return "Emotion: unknown"
    emotion = str(emotion_status.get("emotion", "unknown") or "unknown")
    if emotion_status.get("error"):
        return f"Emotion: {emotion}"
    confidence = emotion_status.get("confidence")
    if isinstance(confidence, (float, int)) and emotion not in {"unknown", "no_face", "unavailable"}:
        return f"Emotion: {emotion} ({confidence:.2f})"
    return f"Emotion: {emotion}"


def emotion_color(emotion_status):
    if not isinstance(emotion_status, dict):
        return (180, 180, 180)
    emotion = str(emotion_status.get("emotion", "unknown") or "unknown")
    if emotion in {"unknown", "no_face", "unavailable"}:
        return (180, 180, 180)
    return {
        "happy": (0, 220, 0),
        "surprise": (0, 220, 220),
        "neutral": (220, 220, 220),
        "sad": (255, 180, 0),
        "angry": (0, 0, 255),
        "fear": (180, 0, 255),
        "disgust": (0, 140, 255),
    }.get(emotion, (220, 220, 220))


def draw_debug_overlay(
    frame_bgr,
    gaze_result=None,
    object_detections=None,
    status="LOOKING_ELSEWHERE",
    matched_object=None,
    emotion_status=None,
    show_heatmap=True,
):
    image = frame_bgr.copy()
    if show_heatmap and gaze_result is not None:
        image = overlay_heatmap(image, gaze_result.get("heatmap"))

    if gaze_result is not None:
        face_bbox = gaze_result.get("face_bbox_px")
        if face_bbox is not None:
            x1, y1, x2, y2 = [int(round(v)) for v in face_bbox]
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(image, "face", (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)

        gaze_point = gaze_result.get("gaze_point")
        if face_bbox is not None and gaze_point is not None:
            x1, y1, x2, y2 = face_bbox
            start = (int(round((x1 + x2) / 2.0)), int(round((y1 + y2) / 2.0)))
            end = as_int_point(gaze_point)
            cv2.arrowedLine(image, start, end, (0, 255, 0), 2, tipLength=0.18)
            cv2.circle(image, end, 5, (0, 255, 0), -1)

    for detection in object_detections or []:
        bbox = detection["bbox"]
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        is_match = detection is matched_object
        color = (0, 200, 0) if is_match else (255, 80, 0)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        label = f"{detection['class_name']} {detection['confidence']:.2f}"
        cv2.putText(image, label, (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

    status_color = (0, 220, 0) if status.startswith("LOOKING_AT_OBJECT") else (0, 0, 255)
    cv2.rectangle(image, (8, 8), (min(image.shape[1] - 8, 560), 42), (0, 0, 0), -1)
    cv2.putText(image, status, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.72, status_color, 2)

    emotion_text = emotion_overlay_text(emotion_status)
    emotion_panel_width = min(image.shape[1] - 8, 360)
    cv2.rectangle(image, (8, 48), (emotion_panel_width, 82), (0, 0, 0), -1)
    cv2.putText(
        image,
        emotion_text,
        (16, 72),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        emotion_color(emotion_status),
        2,
    )
    return image

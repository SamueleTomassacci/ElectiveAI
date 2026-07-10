

import numpy as np


def point_in_bbox(point, bbox, margin_px=0):
    if point is None or bbox is None:
        return False
    x,y = float(point[0]), float(point[1])
    x1,y1, x2,y2 = [float(v) for v in bbox]
    margin = float(margin_px)
    return (x1 -margin) <= x <= (x2 + margin) and (y1- margin) <= y <= (y2+margin)


def bbox_area(bbox):
    if bbox is None:
        return 0.0
    x1, y1,x2, y2 = [float(v) for v in bbox]
    return max(0.0, x2-x1)* max(0.0,y2-y1)


def extract_bbox(candidate):
    if isinstance(candidate, dict):
        return candidate.get("bbox") or candidate.get("face_bbox") or candidate.get("face")
    return candidate


def select_largest_face(results):
    if not results:
        return None

    best_index = None
    best_area = 0.0
    for index,candidate in enumerate(results):
        area = bbox_area(extract_bbox(candidate))
        if area > best_area:

            best_area = area
            best_index = index
    return best_index


def heatmap_mass_inside_bbox(heatmap,bbox, image_width, image_height):
    if heatmap is None or bbox is None:
        return 0.0

    heatmap_np = np.asarray(heatmap,dtype=np.float32)
    if heatmap_np.ndim != 2 or heatmap_np.size == 0:
        return 0.0

    total =float(np.maximum(heatmap_np,0.0).sum())
    if total <= 0.0:
        return 0.0

    h, w = heatmap_np.shape
    x1, y1,x2, y2 = [float(v) for v in bbox]
    scale_x = w/float(image_width)
    scale_y = h / float(image_height)

    hx1 = int(np.floor(max(0.0,min(w,x1 *scale_x))))
    hy1 = int(np.floor(max(0.0, min(h,y1* scale_y))))
    hx2 = int(np.ceil(max(0.0,min(w,x2 * scale_x))))

    hy2 = int(np.ceil(max(0.0, min(h,y2*scale_y))))

    if hx2 <= hx1 or hy2 <= hy1:
        return 0.0

    inside = float(np.maximum(heatmap_np[hy1:hy2, hx1:hx2], 0.0).sum())
    return inside /total


def is_gaze_on_object(
    gaze_point,
    heatmap,
    object_bbox,
    image_shape,
    margin_px,
    use_heatmap_ratio_check,
    heatmap_inside_bbox_threshold,
):
    if object_bbox is None:
        return False

    if point_in_bbox(gaze_point, object_bbox,margin_px=margin_px):
        return True

    if not use_heatmap_ratio_check:
        return False

    image_height, image_width = int(image_shape[0]),int(image_shape[1])
    ratio = heatmap_mass_inside_bbox(heatmap, object_bbox, image_width,image_height)
    return ratio >= float(heatmap_inside_bbox_threshold)

import json
import math
import os
import pickle
import re
from collections import defaultdict

import numpy as np
import rclpy
from rclpy.node import Node


DEFAULT_ALIASES = {
    "peluche": ["peluche", "teddy bear", "plush toy", "stuffed animal"],
    "computer": ["computer", "laptop", "laptop computer"],
    "book": ["book"],
}


def normalize_label(label):
    label = str(label).lower().strip()
    label = re.sub(r"[^a-z0-9 ]+", " ", label)
    label = re.sub(r"\b(the|a|an)\b", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def alias_groups(extra_aliases=None):
    groups = {key: list(values) for key, values in DEFAULT_ALIASES.items()}
    for key, values in (extra_aliases or {}).items():
        merged = groups.setdefault(key, [key])
        for value in values:
            if value not in merged:
                merged.append(value)
    lookup = {}
    for canonical, aliases in groups.items():
        for alias in [canonical, *aliases]:
            normalized = normalize_label(alias)
            if normalized:
                lookup[normalized] = canonical
    return groups, lookup


def canonical_label(label, alias_lookup):
    return alias_lookup.get(normalize_label(label), normalize_label(label) or str(label))


def camera_points_from_mask(mask, depth_mm, camera_info, max_points=6000):
    if mask is None:
        return np.empty((0, 3), dtype=np.float64)
    mask = np.asarray(mask)
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    valid_mask = mask > 0
    depth = np.asarray(depth_mm, dtype=np.float32)
    if depth.ndim == 3:
        depth = depth[:, :, 0]
    valid = valid_mask & np.isfinite(depth) & (depth > 0.0)
    ys, xs = np.nonzero(valid)
    if len(xs) == 0:
        return np.empty((0, 3), dtype=np.float64)
    if len(xs) > max_points:
        sample_idx = np.linspace(0, len(xs) - 1, max_points).astype(np.int64)
        xs = xs[sample_idx]
        ys = ys[sample_idx]
    z = depth[ys, xs].astype(np.float64) / 1000.0
    k = camera_info["k"]
    fx, fy, cx, cy = float(k[0]), float(k[4]), float(k[2]), float(k[5])
    x = (xs.astype(np.float64) - cx) * z / fx
    y = (ys.astype(np.float64) - cy) * z / fy
    return np.column_stack((x, y, z))


def transform_points(points, camera_pose):
    if points.size == 0:
        return points
    matrix = np.asarray(camera_pose["matrix"], dtype=np.float64)
    homogeneous = np.column_stack((points, np.ones((len(points),), dtype=np.float64)))
    return (matrix @ homogeneous.T).T[:, :3]


def robust_stats(points):
    if points.size == 0:
        return None
    centroid = np.median(points, axis=0)
    distances = np.linalg.norm(points - centroid, axis=1)
    keep = distances <= np.percentile(distances, 90)
    filtered = points[keep] if np.any(keep) else points
    return {
        "centroid": np.median(filtered, axis=0),
        "min": np.percentile(filtered, 5, axis=0),
        "max": np.percentile(filtered, 95, axis=0),
    }


def merge_distance_for(label, bbox_extent, config):
    horizontal_extent = max(float(bbox_extent[0]), float(bbox_extent[1]))
    if horizontal_extent < 0.25:
        return float(config.get("small_object_distance_m", 0.25))
    if horizontal_extent > 0.8:
        return float(config.get("large_object_distance_m", 0.60))
    return float(config.get("default_distance_m", 0.35))


class GlobalObject:
    def __init__(self, label, aliases, detection):
        self.label = label
        self.aliases = set(aliases)
        self.detections = [detection]
        self.seen_in_views = {detection["view_id"]}
        self.source_detection_ids = {detection["source_key"]}
        self.update_geometry()

    def update_geometry(self):
        points = np.vstack([item["points"] for item in self.detections if item["points"].size > 0])
        stats = robust_stats(points)
        self.centroid = stats["centroid"]
        self.bbox_min = stats["min"]
        self.bbox_max = stats["max"]
        confidences = [float(item.get("confidence", 0.0)) for item in self.detections]
        self.confidence = max(confidences) if confidences else 0.0

    def can_merge(self, detection, config):
        if detection["view_id"] in self.seen_in_views:
            return False
        centroid = detection["centroid"]
        distance = float(np.linalg.norm(self.centroid[:2] - centroid[:2]))
        z_distance = abs(float(self.centroid[2] - centroid[2]))
        bbox_extent = self.bbox_max - self.bbox_min
        threshold = merge_distance_for(self.label, bbox_extent, config)
        return distance <= threshold and z_distance <= float(config.get("z_threshold_m", 0.25))

    def merge(self, detection):
        self.detections.append(detection)
        self.seen_in_views.add(detection["view_id"])
        self.source_detection_ids.add(detection["source_key"])
        self.update_geometry()

    def to_json(self, index):
        object_id = f"{self.label}_{index}"
        return {
            "id": object_id,
            "label": self.label,
            "aliases": sorted(alias for alias in self.aliases if alias != self.label),
            "seen_in_views": sorted(self.seen_in_views),
            "centroid_map": [float(value) for value in self.centroid],
            "bbox_3d_map": {
                "min": [float(value) for value in self.bbox_min],
                "max": [float(value) for value in self.bbox_max],
            },
            "confidence": float(self.confidence),
            "source_detection_ids": sorted(self.source_detection_ids),
        }


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def load_pickle(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "rb") as file_obj:
        return pickle.load(file_obj)


def build_global_scene_text(objects):
    if not objects:
        return "No visual objects were detected in the multi-view scan."
    lines = ["Global multi-view scene objects:"]
    for item in objects:
        aliases = item.get("aliases") or []
        alias_text = f", also known as {', '.join(aliases)}" if aliases else ""
        centroid = item["centroid_map"]
        confidence = item.get("confidence", 0.0)
        lines.append(
            f"- {item['id']}{alias_text}, seen in views {item['seen_in_views']}, "
            f"confidence={confidence:.2f}, "
            f"centroid_map=({centroid[0]:.2f}, {centroid[1]:.2f}, {centroid[2]:.2f})."
        )
    return "\n".join(lines)


def collect_view_detections(manifest, alias_lookup, log=None, min_confidence=0.15):
    raw = []
    for view in manifest.get("views", []):
        view_id = int(view["view_id"])
        view_dir = os.path.dirname(view["rgb_path"])
        detections = load_pickle(os.path.join(view_dir, "detection.pkl"), default={}) or {}
        depth = np.load(view["depth_path"])
        camera_info = load_pickle(view["camera_info_path"])
        camera_pose = load_json(view["camera_pose_map_path"])
        for key, detection in detections.items():
            score = float(detection.get("score", 0.0))
            if score < min_confidence:
                if log:
                    label_raw = detection.get("label", "?")
                    log(
                        f"[MultiViewAggregator] Skipping {label_raw!r} in view {view_id}: "
                        f"confidence {score:.3f} < {min_confidence:.2f}"
                    )
                continue
            label = canonical_label(detection.get("label", ""), alias_lookup)
            camera_points = camera_points_from_mask(detection.get("mask"), depth, camera_info)
            if camera_points.size == 0:
                if log:
                    log(f"[MultiViewAggregator] Skipping {label} in view {view_id}: no valid depth")
                continue
            map_points = transform_points(camera_points, camera_pose)
            stats = robust_stats(map_points)
            if stats is None:
                continue
            raw.append(
                {
                    "source_key": f"view_{view_id:03d}:{key}",
                    "view_id": view_id,
                    "label": label,
                    "original_label": detection.get("label", label),
                    "confidence": score,
                    "points": map_points,
                    "centroid": stats["centroid"],
                    "bbox_min": stats["min"],
                    "bbox_max": stats["max"],
                }
            )
    return raw


def merge_detections(raw_detections, aliases, config):
    by_label = defaultdict(list)
    for detection in raw_detections:
        by_label[detection["label"]].append(detection)

    global_objects = []
    for label, detections in by_label.items():
        label_aliases = aliases.get(label, [label])
        for detection in detections:
            merged = False
            for obj in global_objects:
                if obj.label == label and obj.can_merge(detection, config):
                    obj.merge(detection)
                    merged = True
                    break
            if not merged:
                global_objects.append(GlobalObject(label, label_aliases, detection))
    return global_objects


def run_aggregation(output_root, use_case, merge_config=None, extra_aliases=None, log=None):
    dump_dir = os.path.join(output_root, "empower_output", use_case)
    manifest_path = os.path.join(dump_dir, "views_manifest.json")
    manifest = load_json(manifest_path)
    if manifest is None:
        raise FileNotFoundError(f"views_manifest.json not found: {manifest_path}")
    aliases, alias_lookup = alias_groups(extra_aliases)
    config = {
        "default_distance_m": 0.35,
        "small_object_distance_m": 0.25,
        "large_object_distance_m": 0.60,
        "z_threshold_m": 0.25,
        "min_detection_confidence": 0.15,
    }
    config.update(merge_config or {})
    min_confidence = float(config.get("min_detection_confidence", 0.15))

    raw = collect_view_detections(manifest, alias_lookup, log=log, min_confidence=min_confidence)
    if log:
        log(f"[MultiViewAggregator] Loaded detections from {len(manifest.get('views', []))} views")
    global_objects = merge_detections(raw, aliases, config)
    objects_json = []
    label_counts = defaultdict(int)
    for obj in sorted(global_objects, key=lambda item: item.label):
        index = label_counts[obj.label]
        label_counts[obj.label] += 1
        objects_json.append(obj.to_json(index))
    result = {
        "objects": objects_json,
        "raw_detection_count": len(raw),
        "merge_config": config,
    }
    scene_graph = {
        "objects": objects_json,
        "description": build_global_scene_text(objects_json),
        "relations": [],
    }
    with open(os.path.join(dump_dir, "global_detections.json"), "w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, indent=2)
    with open(os.path.join(dump_dir, "global_scene_graph.json"), "w", encoding="utf-8") as file_obj:
        json.dump(scene_graph, file_obj, indent=2)
    with open(os.path.join(dump_dir, "global_scene_description.txt"), "w", encoding="utf-8") as file_obj:
        file_obj.write(scene_graph["description"].strip() + "\n")
    if log:
        log(
            f"[MultiViewAggregator] Merged {len(raw)} raw detections into "
            f"{len(objects_json)} global objects"
        )
    return result


class MultiViewAggregatorNode(Node):
    def __init__(self):
        super().__init__("empower_multi_view_aggregator")
        self.declare_parameter("use_case", "therapy_task")
        self.declare_parameter("output_root", "/root/exchange/output")
        self.declare_parameter("multi_view.merge.default_distance_m", 0.35)
        self.declare_parameter("multi_view.merge.small_object_distance_m", 0.25)
        self.declare_parameter("multi_view.merge.large_object_distance_m", 0.60)
        self.declare_parameter("multi_view.merge.z_threshold_m", 0.25)
        self.declare_parameter("multi_view.merge.min_detection_confidence", 0.15)
        self._run()

    def _run(self):
        merge_config = {
            "default_distance_m": float(self.get_parameter("multi_view.merge.default_distance_m").value),
            "small_object_distance_m": float(self.get_parameter("multi_view.merge.small_object_distance_m").value),
            "large_object_distance_m": float(self.get_parameter("multi_view.merge.large_object_distance_m").value),
            "z_threshold_m": float(self.get_parameter("multi_view.merge.z_threshold_m").value),
            "min_detection_confidence": float(self.get_parameter("multi_view.merge.min_detection_confidence").value),
        }
        run_aggregation(
            self.get_parameter("output_root").value,
            self.get_parameter("use_case").value,
            merge_config=merge_config,
            log=self.get_logger().info,
        )


def main(args=None):
    rclpy.init(args=args)
    try:
        MultiViewAggregatorNode()
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

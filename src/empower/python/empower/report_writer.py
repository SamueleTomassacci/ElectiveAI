import json
import os
import pickle
import re

from empower.plan_parser import parse_symbolic_plan


def parse_relations(relations_text):
    relations = []
    for raw_line in relations_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*\d+[\).:-]\s*", "", line).strip()
        match = re.match(r"^\((.*?),\s*(.*?),\s*(.*?)\)\s*$", line)
        if not match:
            relations.append({"raw": line})
            continue
        subject, relation, object_ = [part.strip() for part in match.groups()]
        relations.append({"subject": subject, "relation": relation, "object": object_})
    return relations


def _json_ready(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def load_pickle(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as file_obj:
            return pickle.load(file_obj)
    except ModuleNotFoundError:
        return default


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(text)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2)


def write_readable_reports(dump_dir):
    planning_data = load_pickle(os.path.join(dump_dir, "planning.pkl"), default={}) or {}
    plan_text = planning_data.get("planning_agent_info", "")
    if not plan_text and os.path.exists(os.path.join(dump_dir, "planning.txt")):
        with open(os.path.join(dump_dir, "planning.txt"), "r", encoding="utf-8") as file_obj:
            plan_text = file_obj.read()

    relations_text = planning_data.get("environment_agent_info", "")
    description_text = planning_data.get("description_agent_info", "")
    detections = load_pickle(os.path.join(dump_dir, "detection.pkl"), default={}) or {}

    relations = parse_relations(relations_text)
    plan = parse_symbolic_plan(plan_text)
    detections_json = {}
    for key, detection in detections.items():
        item = {
            "label": detection.get("label"),
            "bbox": _json_ready(detection.get("bbox")),
        }
        if "mask" in detection:
            mask = detection["mask"]
            item["mask_shape"] = list(mask.shape) if hasattr(mask, "shape") else None
        detections_json[str(key)] = item

    write_text(os.path.join(dump_dir, "relations.txt"), relations_text.strip() + "\n")
    write_json(os.path.join(dump_dir, "relations.json"), relations)
    write_text(os.path.join(dump_dir, "scene_description.txt"), description_text.strip() + "\n")
    write_text(os.path.join(dump_dir, "symbolic_plan.txt"), plan_text.strip() + "\n")
    write_json(os.path.join(dump_dir, "detections.json"), detections_json)

    summary_lines = [
        "EMPOWER readable report",
        "",
        "Relations:",
        relations_text.strip() or "(none)",
        "",
        "Scene description:",
        description_text.strip() or "(none)",
        "",
        "Symbolic plan:",
        plan_text.strip() or "(none)",
        "",
        "Detections:",
    ]
    if detections_json:
        for key, detection in detections_json.items():
            summary_lines.append(f"- {key}: {detection.get('label')} bbox={detection.get('bbox')}")
    else:
        summary_lines.append("(none)")

    write_text(os.path.join(dump_dir, "empower_report.txt"), "\n".join(summary_lines) + "\n")
    return {
        "relations": relations,
        "plan": plan,
        "detections": detections_json,
        "files": [
            "relations.txt",
            "relations.json",
            "scene_description.txt",
            "symbolic_plan.txt",
            "detections.json",
            "empower_report.txt",
        ],
    }

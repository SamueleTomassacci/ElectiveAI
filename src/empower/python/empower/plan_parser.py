import json
import re


ACTION_WORDS = {"NAVIGATE", "GRAB","DROP","PULL","PUSH"}
RELATIONS = [
    "in front of",
    "right to",
    "left to",
    "behind to",
    "beside to",
    "into",
    "near",
    "at",
    "on",
]


CHILD_ALIASES = {"child", "the child","kid"}


def normalize_child_alias(value):
    normalized= str(value).lower().strip()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+"," ",normalized).strip()
    if normalized in CHILD_ALIASES:
        return "child"
    return value.strip() if isinstance(value, str) else value


def parse_symbolic_plan(plan_text):
    steps = []
    for raw_line in plan_text.splitlines():
        line = raw_line.strip().strip("'\"")
        if not line or line.startswith("***"):
            continue
        line = re.sub(r"^\s*(?:\d+[\).:-]\s*)+","", line).strip()
        if not line:
            continue
        tokens= line.split()
        action = tokens[0].upper().rstrip(":")
        if action not in ACTION_WORDS:
            continue
        payload = " ".join(tokens[1:]).strip()

        item = {"step": len(steps) + 1,"action": action, "description": line}
        if action == "NAVIGATE":
            target = re.sub(r"^to\s+","",payload,flags=re.IGNORECASE).strip()
            item["target"] = normalize_child_alias(target)
        elif action in {"GRAB","PULL", "PUSH"}:
            item["target"] = payload
        elif action == "DROP":
            target = payload
            for relation in RELATIONS:
                marker = f" {relation} "

                if marker in f" {payload} ":

                    before, after = payload.split(relation, 1)
                    target = before.strip()

                    item["spatial_relation"] = relation
                    item["reference"] = normalize_child_alias(after.strip())
                    break
            item["target"] = target
        steps.append(item)
    return steps


def write_plan_json(plan_text, output_path):
    plan = parse_symbolic_plan(plan_text)
    with open(output_path, "w",encoding="utf-8") as plan_file:
        json.dump(plan, plan_file,indent=2)
    return plan

import json
import os
import re
from copy import deepcopy


class StaticSceneGraphError(ValueError):
    pass


class StaticSceneGraph:
    """Resolver for manually-authored static semantic pose graphs."""

    def __init__(self, data, source_path=""):
        self.source_path = source_path
        self.data = data
        self.scene = data["scene"]
        self.nodes = data["nodes"]
        self.edges = data.get("edges", [])
        self.frame_id = str(self.scene["frame_id"]).strip()
        self._alias_to_node = {}
        self._build_alias_index()

    @classmethod
    def from_json(cls, path):
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        cls.validate(data, source_path=path)
        return cls(data, source_path=path)

    @staticmethod
    def validate(data, source_path=""):
        location = f" in {source_path}" if source_path else ""
        if not isinstance(data, dict):
            raise StaticSceneGraphError(f"Static scene graph{location} must be a mapping")
        scene = data.get("scene")
        nodes = data.get("nodes")
        edges = data.get("edges", [])
        if not isinstance(scene, dict):
            raise StaticSceneGraphError(f"Static scene graph{location} is missing scene")
        if not str(scene.get("frame_id", "")).strip():
            raise StaticSceneGraphError(f"Static scene graph{location} is missing scene.frame_id")
        if not isinstance(nodes, dict) or not nodes:
            raise StaticSceneGraphError(f"Static scene graph{location} must define nodes")
        if not isinstance(edges, list):
            raise StaticSceneGraphError(f"Static scene graph{location} edges must be a list")

        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                raise StaticSceneGraphError(f"Node {node_id} must be a mapping")
            node_type = str(node.get("type", "")).strip()
            if not node_type:
                raise StaticSceneGraphError(f"Node {node_id} is missing type")
            if "pose" in node:
                StaticSceneGraph._validate_pose(node["pose"], node_id, z_required=False)
            if "drop_pose" in node:
                StaticSceneGraph._validate_pose(node["drop_pose"], node_id, z_required=True)
            if node_type == "nav_pose":
                if "pose" not in node:
                    raise StaticSceneGraphError(f"Navigation pose {node_id} is missing pose")
                if not str(node.get("target", "")).strip():
                    raise StaticSceneGraphError(f"Navigation pose {node_id} is missing target")
            if node_type == "waypoint" and "pose" not in node:
                raise StaticSceneGraphError(f"Waypoint {node_id} is missing pose")

        for node_id, node in nodes.items():
            target = node.get("target")
            if target and target not in nodes:
                raise StaticSceneGraphError(f"Node {node_id} targets unknown node {target}")
            for waypoint in node.get("via", []) or []:
                if waypoint not in nodes:
                    raise StaticSceneGraphError(f"Node {node_id} uses unknown waypoint {waypoint}")
                if nodes[waypoint].get("type") != "waypoint":
                    raise StaticSceneGraphError(f"Node {node_id} via entry {waypoint} is not a waypoint")

        for edge in edges:
            if not isinstance(edge, dict):
                raise StaticSceneGraphError("Each edge must be a mapping")
            for key in ("subject", "predicate", "object"):
                if not str(edge.get(key, "")).strip():
                    raise StaticSceneGraphError(f"Edge is missing {key}: {edge}")
            if edge["subject"] not in nodes:
                raise StaticSceneGraphError(f"Edge subject {edge['subject']} is not a node")

    @staticmethod
    def _validate_pose(pose, node_id, z_required):
        if not isinstance(pose, dict):
            raise StaticSceneGraphError(f"Pose for {node_id} must be a mapping")
        required = ("x", "y", "yaw")
        if z_required:
            required = ("x", "y", "z", "yaw")
        for key in required:
            if key not in pose:
                raise StaticSceneGraphError(f"Pose for {node_id} is missing {key}")
            try:
                float(pose[key])
            except (TypeError, ValueError) as exc:
                raise StaticSceneGraphError(f"Pose value {node_id}.{key} must be numeric") from exc

    @staticmethod
    def normalize(value):
        value = str(value).lower().strip()
        value = re.sub(r"[^a-z0-9 ]+", " ", value)
        value = re.sub(r"\b(the|a|an)\b", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def _build_alias_index(self):
        for node_id, node in self.nodes.items():
            names = {node_id, node_id.replace("_", " ")}
            label = node.get("label")
            if label:
                names.add(str(label))
            for alias in node.get("aliases", []) or []:
                names.add(str(alias))
            for name in names:
                normalized = self.normalize(name)
                if normalized:
                    self._alias_to_node.setdefault(normalized, node_id)

    def resolve_node(self, name, expected_type=None):
        normalized = self.normalize(name)
        node_id = self._alias_to_node.get(normalized)
        if node_id is None and str(name) in self.nodes:
            node_id = str(name)
        if node_id is None:
            return None
        node = self.nodes[node_id]
        if expected_type is not None and node.get("type") != expected_type:
            return None
        resolved = deepcopy(node)
        resolved["id"] = node_id
        return resolved

    def resolve_node_id(self, name, expected_type=None):
        node = self.resolve_node(name, expected_type=expected_type)
        if node is None:
            return None
        return node["id"]

    def resolve_nav_pose(self, target, role=None):
        target_id = self.resolve_node_id(target)
        role = self.normalize_role(role)
        target_node = self.nodes.get(target_id or "")
        if target_node and "pose" in target_node:
            if not role or role in self.normalized_roles(target_node):
                return self.pose_dict(target_node["pose"], include_z=False, node_id=target_id)
        for node_id, node in self.nodes.items():
            if node.get("type") != "nav_pose":
                continue
            target_matches = node_id == target_id or node.get("target") == target_id
            if not target_matches:
                continue
            if role and role not in self.normalized_roles(node):
                continue
            return self.pose_dict(node["pose"], include_z=False, node_id=node_id)
        return None

    def resolve_waypoints_for_nav_pose(self, target, role=None):
        target_id = self.resolve_node_id(target)
        role = self.normalize_role(role)
        waypoints = []
        for node_id, node in self.nodes.items():
            if node.get("type") != "nav_pose":
                continue
            target_matches = node_id == target_id or node.get("target") == target_id
            if not target_matches:
                continue
            if role and role not in self.normalized_roles(node):
                continue
            for waypoint_id in node.get("via", []) or []:
                waypoint = self.nodes.get(waypoint_id)
                if waypoint and waypoint.get("type") == "waypoint":
                    waypoints.append(self.pose_dict(waypoint["pose"], include_z=False, node_id=waypoint_id))
            return waypoints
        return []

    def resolve_drop_pose(self, role=None):
        role = self.normalize_role(role)
        for node_id, node in self.nodes.items():
            if "drop_pose" not in node:
                continue
            if role and role not in self.normalized_roles(node):
                continue
            return self.pose_dict(node["drop_pose"], include_z=True, node_id=node_id)
        return None

    def aliases_for(self, name):
        node = self.resolve_node(name)
        if node is None:
            return []
        aliases = [node["id"], node["id"].replace("_", " ")]
        if node.get("label"):
            aliases.append(str(node["label"]))
        aliases.extend(str(alias) for alias in node.get("aliases", []) or [])
        deduped = []
        seen = set()
        for alias in aliases:
            normalized = self.normalize(alias)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(alias)
        return deduped

    def is_alias_of(self, alias, canonical):
        alias_id = self.resolve_node_id(alias)
        canonical_id = self.resolve_node_id(canonical)
        return alias_id is not None and alias_id == canonical_id

    def normalized_roles(self, node):
        return {self.normalize_role(role) for role in node.get("roles", []) or []}

    @staticmethod
    def normalize_role(role):
        if role is None:
            return ""
        return str(role).strip().lower()

    @staticmethod
    def pose_dict(pose, include_z, node_id):
        result = {
            "id": node_id,
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "yaw": float(pose["yaw"]),
        }
        if include_z:
            result["z"] = float(pose["z"])
        return result


def default_scene_graph_path():
    try:
        from ament_index_python.packages import get_package_share_directory

        return os.path.join(
            get_package_share_directory("empower"),
            "config",
            "scenes",
            "therapy_room_scene_graph.json",
        )
    except Exception:
        return os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "config",
                "scenes",
                "therapy_room_scene_graph.json",
            )
        )

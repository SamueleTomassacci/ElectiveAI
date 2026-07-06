"""Gazebo speech bubbles for the HRI dialogue topics."""

import json
import math
import re
import shutil
import subprocess
import time
from html import escape
from pathlib import Path

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import Pose
from rclpy.node import Node
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener


class BubbleEntity:
    def __init__(self, name, anchor, created_at, expires_at):
        self.name = name
        self.anchor = anchor
        self.created_at = created_at
        self.expires_at = expires_at


def _as_float_list(value, expected_len, fallback):
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return fallback
    if len(result) != expected_len:
        return fallback
    return result


def _wrap_text(text, max_chars, max_lines):
    words = text.strip().split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".") + "..."
    return lines[:max_lines]


class DialogBubbleNode(Node):
    """Render /hri utterances as speech bubbles inside Gazebo classic."""

    def __init__(self):
        super().__init__("dialog_bubble_node")
        self.declare_parameters(
            namespace="",
            parameters=[
                ("enabled", True),
                ("world_frame", "map"),
                ("robot_frame", "base_footprint"),
                ("robot_anchor_xyz", [0.0, 0.0, 2.15]),
                ("child_anchor_xyz", [0.0, 2.55, 1.75]),
                ("adult_anchor_xyz", [0.0, -2.75, 2.15]),
                ("robot_offset_xyz", [0.0, 0.0, 2.15]),
                ("bubble_yaw", 1.35),
                ("bubble_width_m", 1.9),
                ("bubble_height_m", 0.75),
                ("bubble_lifetime_sec", 9.0),
                ("pose_update_hz", 1.0),
                ("max_chars_per_line", 34),
                ("max_lines", 3),
                ("output_dir", "/root/exchange/exchange/models/hri_dialog_bubbles"),
                ("gazebo_command", "gz"),
                ("gazebo_world_name", "scene"),
                ("gazebo_cli_timeout_sec", 5.0),
                ("user_anchor", "auto"),
                ("show_state_thinking", True),
                ("thinking_text", "Thinking..."),
                ("robot_label", "TIAGo"),
                ("user_label_child", "Child"),
                ("user_label_adult", "Therapist"),
            ],
        )

        self.enabled = bool(self.get_parameter("enabled").value)
        self.world_frame = str(self.get_parameter("world_frame").value)
        self.robot_frame = str(self.get_parameter("robot_frame").value)
        self.output_dir = Path(str(self.get_parameter("output_dir").value)).expanduser().resolve()
        self.texture_dir = self.output_dir / "materials" / "textures"
        self.generated_dir = self.output_dir / "generated"
        self.run_id = int(time.time() * 1000)
        self.message_counter = 0
        self.current = {}
        self.mission_state = ""
        self.dialog_state = ""
        self.last_robot_text = ""

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.create_subscription(String, "/hri/robot_utterance", self.robot_utterance_callback, 10)
        self.create_subscription(String, "/hri/user_utterance", self.user_utterance_callback, 10)
        self.create_subscription(String, "/hri/dialog_state", self.dialog_state_callback, 10)
        self.create_subscription(String, "/mission/state", self.mission_state_callback, 10)
        self.ready_publisher = self.create_publisher(String, "/hri/dialog_bubble_ready", 10)

        update_period = 1.0 / max(1.0, float(self.get_parameter("pose_update_hz").value))
        self.create_timer(update_period, self.update_bubbles)

        if self.enabled:
            self.prepare_output_dir()
            self.get_logger().info("Gazebo HRI speech bubbles enabled.")
        else:
            self.get_logger().info("Gazebo HRI speech bubbles disabled.")

    def prepare_output_dir(self):
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.texture_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "model.config").write_text(
            """<?xml version="1.0"?>
<model>
  <name>hri_dialog_bubbles</name>
  <version>1.0</version>
  <sdf version="1.6">model.sdf</sdf>
  <author>
    <name>ElectiveAI</name>
  </author>
  <description>Runtime-generated HRI dialogue bubble materials.</description>
</model>
""",
            encoding="utf-8",
        )
        (self.output_dir / "model.sdf").write_text(
            """<?xml version="1.0"?>
<sdf version="1.6">
  <model name="hri_dialog_bubbles">
    <static>true</static>
  </model>
</sdf>
""",
            encoding="utf-8",
        )

    def robot_utterance_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        self.last_robot_text = text
        self.show_bubble("robot", text, str(self.get_parameter("robot_label").value))

    def user_utterance_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        anchor = self.resolve_user_anchor()
        label_param = "user_label_child" if anchor == "child" else "user_label_adult"
        self.show_bubble(anchor, text, str(self.get_parameter(label_param).value))

    def dialog_state_callback(self, msg):
        state = msg.data.strip()
        if state == self.dialog_state:
            return
        self.dialog_state = state
        if not bool(self.get_parameter("show_state_thinking").value):
            return
        if state != "thinking":
            return
        text = str(self.get_parameter("thinking_text").value)
        if text:
            self.show_bubble("robot", text, str(self.get_parameter("robot_label").value), short=True)

    def mission_state_callback(self, msg):
        self.mission_state = msg.data.strip()

    def resolve_user_anchor(self):
        mode = str(self.get_parameter("user_anchor").value).strip().lower()
        if mode in {"child", "adult"}:
            return mode
        if self.mission_state == "CHILD_ACTIVITY":
            return "child"
        return "adult"

    def show_bubble(self, anchor, text, label, short = False):
        if not self.enabled:
            return
        self.message_counter += 1
        entity_name = f"hri_dialog_bubble_{anchor}_{self.run_id}_{self.message_counter}"
        texture_path = self.texture_dir / f"{entity_name}.png"
        mesh_path = self.generated_dir / f"{entity_name}.dae"

        lines = _wrap_text(
            text,
            int(self.get_parameter("max_chars_per_line").value),
            int(self.get_parameter("max_lines").value),
        )
        self.write_bubble_texture(texture_path, label, lines, anchor)
        self.write_bubble_mesh(mesh_path, texture_path, entity_name)
        sdf = self.build_bubble_sdf(entity_name, mesh_path)
        if not sdf.strip():
            self.get_logger().error(
                f"Refusing to spawn empty SDF for dialogue bubble '{entity_name}'."
            )
            return
        sdf_path = self.generated_dir / f"{entity_name}.sdf"
        sdf_path.write_text(sdf, encoding="utf-8")
        pose = self.pose_for_anchor(anchor)

        self.delete_anchor(anchor)
        self.get_logger().info(
            f"Spawning dialogue bubble '{entity_name}' from {sdf_path} "
            f"with {len(sdf)} SDF characters."
        )
        if not self.spawn_entity(entity_name, sdf_path, pose):
            return
        now = time.monotonic()
        self.current[anchor] = BubbleEntity(
            name=entity_name,
            anchor=anchor,
            created_at=now,
            expires_at=now + self.lifetime(short),
        )
        self.publish_ready(anchor, entity_name, text)

    def publish_ready(self, anchor, entity_name, text):
        payload = {
            "anchor": anchor,
            "entity": entity_name,
            "text": text,
        }
        message = String()
        message.data = json.dumps(payload)
        self.ready_publisher.publish(message)

    def lifetime(self, short):
        base = float(self.get_parameter("bubble_lifetime_sec").value)
        return min(base, 2.5) if short else base

    def delete_anchor(self, anchor):
        bubble = self.current.pop(anchor, None)
        if bubble is None:
            return
        self.delete_entity(bubble.name)

    def delete_entity(self, entity_name):
        command = self.gz_model_command()
        command.extend(["-m", entity_name, "-d"])
        self.run_gz_command(command, "DeleteBubble")

    def update_bubbles(self):
        if not self.enabled:
            return
        now = time.monotonic()
        for anchor, bubble in list(self.current.items()):
            if now > bubble.expires_at:
                self.current.pop(anchor, None)
                self.delete_entity(bubble.name)
                continue
            self.set_entity_pose(bubble.name, self.pose_for_anchor(anchor))

    def set_entity_pose(self, entity_name, pose):
        command = self.gz_model_command()
        command.extend(self.pose_args(entity_name, pose))
        self.run_gz_command(command, "MoveBubble")

    def spawn_entity(self, entity_name, sdf_path, pose):
        command = self.gz_model_command()
        command.extend(["-m", entity_name, "-f", str(sdf_path)])
        command.extend(self.pose_args("", pose, include_model=False))
        return self.run_gz_command(command, "SpawnBubble")

    def gz_model_command(self):
        command = [str(self.get_parameter("gazebo_command").value), "model"]
        world_name = str(self.get_parameter("gazebo_world_name").value).strip()
        if world_name:
            command.extend(["-w", world_name])
        return command

    def pose_args(self, entity_name, pose, include_model = True):
        yaw = self.yaw_from_pose(pose)
        args = []
        if include_model:
            args.extend(["-m", entity_name])
        args.extend(
            [
                "-x",
                f"{pose.position.x:.6f}",
                "-y",
                f"{pose.position.y:.6f}",
                "-z",
                f"{pose.position.z:.6f}",
                "-R",
                "0.000000",
                "-P",
                "0.000000",
                "-Y",
                f"{yaw:.6f}",
            ]
        )
        return args

    @staticmethod
    def yaw_from_pose(pose):
        return math.atan2(
            2.0 * pose.orientation.w * pose.orientation.z,
            1.0 - 2.0 * pose.orientation.z * pose.orientation.z,
        )

    def run_gz_command(self, command, label):
        log_method = self.get_logger().debug if label == "MoveBubble" else self.get_logger().info
        log_method(f"{label} Gazebo command: {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=float(self.get_parameter("gazebo_cli_timeout_sec").value),
            )
        except FileNotFoundError:
            self.get_logger().error(f"{label} failed: '{command[0]}' was not found.")
            return False
        except subprocess.TimeoutExpired:
            self.get_logger().error(f"{label} Gazebo command timed out.")
            return False

        if result.stdout.strip():
            self.get_logger().info(f"{label} Gazebo stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            self.get_logger().warning(f"{label} Gazebo stderr: {result.stderr.strip()}")
        if result.returncode != 0:
            self.get_logger().error(
                f"{label} Gazebo command failed with exit code {result.returncode}."
            )
            return False
        return True

    def pose_for_anchor(self, anchor):
        if anchor == "robot":
            pose = self.robot_pose_from_tf()
            if pose is not None:
                return pose
            xyz = _as_float_list(
                self.get_parameter("robot_anchor_xyz").value,
                3,
                [0.0, 0.0, 2.15],
            )
        elif anchor == "child":
            xyz = _as_float_list(
                self.get_parameter("child_anchor_xyz").value,
                3,
                [0.0, 2.55, 1.75],
            )
        else:
            xyz = _as_float_list(
                self.get_parameter("adult_anchor_xyz").value,
                3,
                [0.0, -2.75, 2.15],
            )
        return self.make_pose(xyz[0], xyz[1], xyz[2])

    def robot_pose_from_tf(self):
        offset = _as_float_list(
            self.get_parameter("robot_offset_xyz").value,
            3,
            [0.0, 0.0, 2.15],
        )
        try:
            transform = self.tf_buffer.lookup_transform(
                self.world_frame,
                self.robot_frame,
                rclpy.time.Time(),
            )
        except TransformException:
            return None
        translation = transform.transform.translation
        return self.make_pose(
            translation.x + offset[0],
            translation.y + offset[1],
            translation.z + offset[2],
        )

    def make_pose(self, x, y, z):
        yaw = float(self.get_parameter("bubble_yaw").value)
        pose = Pose()
        pose.position.x = float(x)
        pose.position.y = float(y)
        pose.position.z = float(z)
        pose.orientation.z = math.sin(yaw * 0.5)
        pose.orientation.w = math.cos(yaw * 0.5)
        return pose

    def write_bubble_texture(self, path, label, lines, anchor):
        width, height = 1024, 448
        image = np.full((height, width, 3), (255, 255, 255), dtype=np.uint8)

        fill = (255, 255, 255)
        border = (205, 211, 218)
        label_color = (175, 82, 30) if anchor == "robot" else (70, 130, 48)
        text_color = (34, 37, 41)

        rect_left, rect_top = 28, 28
        rect_right, rect_bottom = width - 28, height - 72
        radius = 34
        self.rounded_rectangle(image, rect_left, rect_top, rect_right, rect_bottom, radius, fill)
        self.rounded_rectangle_outline(
            image, rect_left, rect_top, rect_right, rect_bottom, radius, border, 6
        )

        tail = np.array(
            [
                [width // 2 - 42, rect_bottom - 3],
                [width // 2 + 42, rect_bottom - 3],
                [width // 2, height - 28],
            ],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(image, tail, fill, lineType=cv2.LINE_AA)
        cv2.polylines(image, [tail], True, border, 6, lineType=cv2.LINE_AA)

        cv2.putText(
            image,
            label,
            (72, 98),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.35,
            label_color,
            4,
            lineType=cv2.LINE_AA,
        )
        y = 178
        for line in lines:
            cv2.putText(
                image,
                line,
                (72, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.32,
                text_color,
                3,
                lineType=cv2.LINE_AA,
            )
            y += 76
        cv2.imwrite(str(path), image)

    @staticmethod
    def rounded_rectangle(
        image,
        left,
        top,
        right,
        bottom,
        radius,
        color,
    ):
        cv2.rectangle(image, (left + radius, top), (right - radius, bottom), color, -1)
        cv2.rectangle(image, (left, top + radius), (right, bottom - radius), color, -1)
        for cx, cy in (
            (left + radius, top + radius),
            (right - radius, top + radius),
            (left + radius, bottom - radius),
            (right - radius, bottom - radius),
        ):
            cv2.circle(image, (cx, cy), radius, color, -1, lineType=cv2.LINE_AA)

    @staticmethod
    def rounded_rectangle_outline(
        image,
        left,
        top,
        right,
        bottom,
        radius,
        color,
        thickness,
    ):
        cv2.line(image, (left + radius, top), (right - radius, top), color, thickness)
        cv2.line(image, (left + radius, bottom), (right - radius, bottom), color, thickness)
        cv2.line(image, (left, top + radius), (left, bottom - radius), color, thickness)
        cv2.line(image, (right, top + radius), (right, bottom - radius), color, thickness)
        for center, start, end in (
            ((left + radius, top + radius), 180, 270),
            ((right - radius, top + radius), 270, 360),
            ((left + radius, bottom - radius), 90, 180),
            ((right - radius, bottom - radius), 0, 90),
        ):
            cv2.ellipse(image, center, (radius, radius), 0, start, end, color, thickness)

    def write_bubble_mesh(self, path, texture_path, entity_name):
        width = float(self.get_parameter("bubble_width_m").value)
        height = float(self.get_parameter("bubble_height_m").value)
        half_w = width * 0.5
        half_h = height * 0.5
        texture_uri = escape(texture_path.resolve().as_uri(), quote=True)
        safe_id = re.sub(r"[^A-Za-z0-9_]+", "_", entity_name)
        image_id = f"{safe_id}_image"
        effect_id = f"{safe_id}_effect"
        material_id = f"{safe_id}_material"
        geometry_id = f"{safe_id}_geometry"
        positions_id = f"{safe_id}_positions"
        positions_array_id = f"{safe_id}_positions_array"
        uvs_id = f"{safe_id}_uvs"
        uvs_array_id = f"{safe_id}_uvs_array"
        vertices_id = f"{safe_id}_vertices"
        scene_id = f"{safe_id}_scene"
        node_id = f"{safe_id}_node"
        # A tiny Collada plane with an inline texture avoids Gazebo classic's
        # runtime material-script cache. Both triangle windings are included so
        # the bubble remains visible from either side of the camera path.
        path.write_text(
            f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset>
    <unit name="meter" meter="1"/>
    <up_axis>Z_UP</up_axis>
  </asset>
  <library_images>
    <image id="{image_id}" name="{image_id}">
      <init_from>{texture_uri}</init_from>
    </image>
  </library_images>
  <library_effects>
    <effect id="{effect_id}">
      <profile_COMMON>
        <newparam sid="bubble_surface">
          <surface type="2D">
            <init_from>{image_id}</init_from>
          </surface>
        </newparam>
        <newparam sid="bubble_sampler">
          <sampler2D>
            <source>bubble_surface</source>
          </sampler2D>
        </newparam>
        <technique sid="common">
          <lambert>
            <emission>
              <color>0.35 0.35 0.35 1</color>
            </emission>
            <ambient>
              <color>1 1 1 1</color>
            </ambient>
            <diffuse>
              <texture texture="bubble_sampler" texcoord="UVSET0"/>
            </diffuse>
            <transparency>
              <float>1.0</float>
            </transparency>
          </lambert>
        </technique>
        <extra>
          <technique profile="GOOGLEEARTH">
            <double_sided>1</double_sided>
          </technique>
        </extra>
      </profile_COMMON>
    </effect>
  </library_effects>
  <library_materials>
    <material id="{material_id}" name="{material_id}">
      <instance_effect url="#{effect_id}"/>
    </material>
  </library_materials>
  <library_geometries>
    <geometry id="{geometry_id}" name="{geometry_id}">
      <mesh>
        <source id="{positions_id}">
          <float_array id="{positions_array_id}" count="12">
            0 {-half_w:.6f} {-half_h:.6f}
            0 {half_w:.6f} {-half_h:.6f}
            0 {half_w:.6f} {half_h:.6f}
            0 {-half_w:.6f} {half_h:.6f}
          </float_array>
          <technique_common>
            <accessor source="#{positions_array_id}" count="4" stride="3">
              <param name="X" type="float"/>
              <param name="Y" type="float"/>
              <param name="Z" type="float"/>
            </accessor>
          </technique_common>
        </source>
        <source id="{uvs_id}">
          <float_array id="{uvs_array_id}" count="16">
            0 0
            1 0
            1 1
            0 1
            1 0
            0 0
            0 1
            1 1
          </float_array>
          <technique_common>
            <accessor source="#{uvs_array_id}" count="8" stride="2">
              <param name="S" type="float"/>
              <param name="T" type="float"/>
            </accessor>
          </technique_common>
        </source>
        <vertices id="{vertices_id}">
          <input semantic="POSITION" source="#{positions_id}"/>
        </vertices>
        <triangles material="{material_id}" count="4">
          <input semantic="VERTEX" source="#{vertices_id}" offset="0"/>
          <input semantic="TEXCOORD" source="#{uvs_id}" offset="1" set="0"/>
          <p>
            0 0 1 1 2 2
            0 0 2 2 3 3
            2 6 1 5 0 4
            3 7 2 6 0 4
          </p>
        </triangles>
      </mesh>
    </geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="{scene_id}" name="{scene_id}">
      <node id="{node_id}" name="{node_id}">
        <instance_geometry url="#{geometry_id}">
          <bind_material>
            <technique_common>
              <instance_material symbol="{material_id}" target="#{material_id}"/>
            </technique_common>
          </bind_material>
        </instance_geometry>
      </node>
    </visual_scene>
  </library_visual_scenes>
  <scene>
    <instance_visual_scene url="#{scene_id}"/>
  </scene>
</COLLADA>
""",
            encoding="utf-8",
        )

    def build_bubble_sdf(self, entity_name, mesh_path):
        mesh_uri = mesh_path.resolve().as_uri()
        return f"""<?xml version="1.0"?>
<sdf version="1.6">
  <model name="{entity_name}">
    <static>false</static>
    <link name="bubble">
      <gravity>false</gravity>
      <kinematic>true</kinematic>
      <visual name="bubble_visual">
        <geometry>
          <mesh>
            <uri>{mesh_uri}</uri>
          </mesh>
        </geometry>
        <transparency>0.0</transparency>
      </visual>
    </link>
  </model>
</sdf>
"""


def main(args=None):
    rclpy.init(args=args)
    node = DialogBubbleNode()
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

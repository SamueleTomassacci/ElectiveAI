import importlib
import json
import os
import socket
import threading
import traceback

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node

from empower_ros2.loader_adapter import EmpowerRos2Loader


class ModelsCacherNode(Node):
    """ROS2 adapter for empower's models_cacher.py socket workflow."""

    def __init__(self):
        super().__init__("empower_models_cacher")
        self.declare_parameter("use_case", "tiago_task")
        self.declare_parameter("task_description", "")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("config_dir", "")
        self.declare_parameter("yolow_path", "")
        self.declare_parameter("encoder_path", "")
        self.declare_parameter("decoder_path", "")
        self.declare_parameter("spacy_model", "en_core_web_sm")
        self.declare_parameter("word2vec_model", "word2vec-google-news-300")
        self.declare_parameter("server_host", "127.0.0.1")
        self.declare_parameter("server_port", 10000)
        self.declare_parameter("openai_api_key_file", "/root/exchange/openai_api_key.txt")

        package_share = get_package_share_directory("empower_ros2")
        config_dir = (
            self.get_parameter("config_dir").value
            or os.environ.get("EMPOWER_CONFIG_DIR")
            or os.path.join(package_share, "config")
        )
        output_root = self.get_parameter("output_root").value
        self.task_description = self.get_parameter("task_description").value
        self._load_openai_api_key()

        self.get_logger().info("Loading empower models: YOLO-World, EfficientViT-SAM, spaCy and word2vec")
        self.loader = EmpowerRos2Loader(
            use_case=self.get_parameter("use_case").value,
            images_dir=os.path.join(output_root, "images"),
            output_dir=os.path.join(output_root, "output"),
            config_dir=config_dir,
            yolow_path=self.get_parameter("yolow_path").value or None,
            encoder_path=self.get_parameter("encoder_path").value or None,
            decoder_path=self.get_parameter("decoder_path").value or None,
            spacy_model=self.get_parameter("spacy_model").value,
            word2vec_model=self.get_parameter("word2vec_model").value,
        )
        self.server_thread = threading.Thread(target=self._serve, daemon=True)
        self.server_thread.start()
        self.get_logger().info("Model cacher ready")

    def _load_openai_api_key(self):
        if os.environ.get("OPENAI_API_KEY"):
            return
        key_path = self.get_parameter("openai_api_key_file").value
        if not key_path:
            return
        if not os.path.exists(key_path):
            self.get_logger().warn(
                f"OPENAI_API_KEY is not set and key file does not exist: {key_path}"
            )
            return
        with open(key_path, "r", encoding="utf-8") as key_file:
            api_key = key_file.read().strip()
        if not api_key:
            self.get_logger().warn(f"OpenAI API key file is empty: {key_path}")
            return
        os.environ["OPENAI_API_KEY"] = api_key
        self.get_logger().info(f"Loaded OpenAI API key from {key_path}")

    def _serve(self):
        host = self.get_parameter("server_host").value
        port = int(self.get_parameter("server_port").value)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((host, port))
            server_socket.listen(1)
            self.get_logger().info(f"Listening for empower detection triggers on {host}:{port}")
            while rclpy.ok():
                connection, _ = server_socket.accept()
                with connection:
                    message = connection.recv(65536).decode()
                    if not message:
                        continue
                    try:
                        self._handle_message(message)
                        connection.sendall(b"ok")
                    except Exception as exc:
                        self.get_logger().error(f"EMPOWER detection trigger failed: {exc}\n{traceback.format_exc()}")
                        connection.sendall(f"error: {exc}".encode())

    def _handle_message(self, message):
        task_description = self.task_description
        command = message
        try:
            payload = json.loads(message)
            command = payload.get("command", "")
            task_description = payload.get("task_description") or task_description
        except json.JSONDecodeError:
            pass
        if "detection" not in command:
            return

        detection = importlib.import_module("detection")
        importlib.reload(detection)
        detection_instance = detection.Detection()
        if task_description:
            detection_instance.task_dict[self.loader.use_case] = task_description
        elif self.loader.use_case not in detection_instance.task_dict:
            raise KeyError(
                f"No task_description supplied and use_case '{self.loader.use_case}' is not in empower's task_dict"
            )
        detection_instance.set_loader(self.loader)
        self.get_logger().info("Detection, grounding and planning completed")


def main(args=None):
    rclpy.init(args=args)
    node = ModelsCacherNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

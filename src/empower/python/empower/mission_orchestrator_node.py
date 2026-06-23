import json
import math
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String


class MissionState(str, Enum):
    GO_TO_THERAPIST = "GO_TO_THERAPIST"
    WAIT_TASK = "WAIT_TASK"
    ACK_TASK = "ACK_TASK"
    WAIT_ACTIVITY = "WAIT_ACTIVITY"
    ACK_ACTIVITY = "ACK_ACTIVITY"
    WAIT_CHILD_NAME = "WAIT_CHILD_NAME"
    ACK_CHILD_NAME = "ACK_CHILD_NAME"
    GO_TO_SPAWN = "GO_TO_SPAWN"
    RUN_EMPOWER = "RUN_EMPOWER"
    GO_TO_CHILD = "GO_TO_CHILD"
    CHILD_READY = "CHILD_READY"
    CHILD_ACTIVITY = "CHILD_ACTIVITY"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    MISSION_FAILED = "MISSION_FAILED"


@dataclass
class MissionInteractionSpec:
    task_description: str = ""
    activity_description: str = ""
    activity_type: str = "generic_child_activity"
    child_name: str = ""


class MissionOrchestratorNode(Node):
    #Node which orchestrates the entire pipeline

    def __init__(self):
        super().__init__("mission_orchestrator_node")
        self.declare_node_parameters()

        status_qos = QoSProfile(depth=1)
        status_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.state_pub = self.create_publisher(String, "/mission/state", status_qos)
        self.feedback_pub = self.create_publisher(String, "/mission/feedback", 10)
        self.task_pub = self.create_publisher(String, "/mission/task_description", status_qos)
        self.child_ready_pub = self.create_publisher(String, "/mission/child_ready", status_qos)
        self.dialog_command_pub = self.create_publisher(String, "/hri/dialog_command", 10)

        self.create_subscription(String, "/hri/user_utterance", self.task_callback, 10)
        self.create_subscription(String, "/hri/dialog_complete", self.dialog_complete_callback, 10)
        self.create_subscription(String, "/mission/gaze_command", self.gaze_command_callback, 10)
        self.create_subscription(String, "/gaze/node_status", self.gaze_node_status_callback, 10)

        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            str(self.get_parameter("nav_action_name").value),
        )
        self._state_lock = threading.Lock()
        self._state = None
        self._interaction_spec = MissionInteractionSpec(
            activity_type=str(self.get_parameter("default_activity_type").value)
        )
        self._task_event = threading.Event()
        self._activity_event = threading.Event()
        self._child_name_event = threading.Event()
        self.child_name = ""
        self._stop_event = threading.Event()
        self._dialog_complete_event = threading.Event()
        self._dialog_complete_payload = ""
        self._mission_thread = None
        self._gaze_process = None
        self._gaze_process_label = "gaze estimation"
        self._gaze_ready_event = threading.Event()
        self._gaze_error_message = ""

        if bool(self.get_parameter("auto_start").value):
            self._mission_thread = threading.Thread(
                target=self.run_mission,
                name="hri_empower_mission",
                daemon=True,
            )
            self._mission_thread.start()
        else:
            self.publish_feedback("Mission orchestrator ready; auto_start is disabled")

    def declare_node_parameters(self):
        self.declare_parameter("therapist_pose", [0.0, -1.6, -1.5708])
        self.declare_parameter("spawn_pose", [0.0, 0.0, 0.0])
        self.declare_parameter("child_pose", [0.0, 1.6, 1.5708])
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter(
            "params_file",
            "/root/exchange/src/empower/config/empower_config.yaml",
        )
        self.declare_parameter("wait_for_task_timeout_sec", 120.0)
        self.declare_parameter("wait_for_activity_timeout_sec", 120.0)
        self.declare_parameter("wait_for_child_name_timeout_sec", 120.0)
        self.declare_parameter("task_ack_text", "Okay, I understood the task.")
        self.declare_parameter(
            "activity_ack_text",
            "Okay, I understood the activity. What is the child's name?",
        )
        self.declare_parameter(
            "child_name_ack_text",
            "Okay, I will personalize the activity for {child_name}.",
        )
        self.declare_parameter("default_activity_type", "generic_child_activity")
        self.declare_parameter("auto_start", True)
        self.declare_parameter("action_server_timeout_sec", 20.0)
        self.declare_parameter("nav_result_timeout_sec", 300.0)
        self.declare_parameter("empower_process_timeout_sec", 0.0)
        self.declare_parameter("capture_timeout_sec", 60.0)
        self.declare_parameter("capture_before_empower", True)
        self.declare_parameter("execute_empower_plan", True)
        self.declare_parameter("child_delivery_handled_by_empower", False)
        self.declare_parameter("use_case", "therapy_task")
        self.declare_parameter("output_root", "/root/exchange/output")
        self.declare_parameter("storytelling_timeout_sec", 300.0)
        self.declare_parameter("child_activity_timeout_sec", 300.0)
        self.declare_parameter("gaze_estimation.enabled", True)
        self.declare_parameter("gaze_estimation.camera_index", 0)
        self.declare_parameter("gaze_estimation.device", "auto")
        self.declare_parameter("gaze_estimation.show_window", True)
        self.declare_parameter("gaze_estimation.startup_timeout_sec", 30.0)

    def task_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        state = self.current_state()
        if state not in {
            MissionState.WAIT_TASK,
            MissionState.WAIT_ACTIVITY,
            MissionState.WAIT_CHILD_NAME,
        }:
            self.publish_feedback(
                f"Ignoring HRI utterance outside therapist input capture: {text}",
                log_level="debug",
            )
            return
        if state == MissionState.WAIT_TASK:
            self.capture_physical_task(text)
            return
        if state == MissionState.WAIT_ACTIVITY:
            self.capture_child_activity(text)
            return
        if state == MissionState.WAIT_CHILD_NAME:
            self.capture_child_name(text)

    def capture_physical_task(self, text):
        if self._interaction_spec.task_description:
            self.publish_feedback(
                f"Ignoring extra HRI utterance after task capture: {text}",
                log_level="debug",
            )
            return
        self._interaction_spec.task_description = text
        self.publish(self.task_pub, text)
        self.publish_feedback(f"Received physical task: {text}")
        self._task_event.set()

    def capture_child_activity(self, text):
        if self._interaction_spec.activity_description:
            self.publish_feedback(
                f"Ignoring extra HRI utterance after activity capture: {text}",
                log_level="debug",
            )
            return
        self._interaction_spec.activity_description = text
        self._interaction_spec.activity_type = self.normalize_activity_type(text)
        self.publish_feedback(f"Received child activity: {text}")
        self.publish_feedback(
            f"Normalized activity type: {self._interaction_spec.activity_type}"
        )
        self._activity_event.set()

    def capture_child_name(self, text):
        if self._interaction_spec.child_name:
            self.publish_feedback(
                f"Ignoring extra HRI utterance after child name capture: {text}",
                log_level="debug",
            )
            return
        child_name = text.strip().strip(".!?;:,")
        self._interaction_spec.child_name = child_name
        self.child_name = child_name
        self.publish_feedback(f"Received child name: {child_name}")
        self._child_name_event.set()

    def dialog_complete_callback(self, msg):
        self._dialog_complete_payload = msg.data
        self._dialog_complete_event.set()

    def run_mission(self):
        try:
            self.transition(MissionState.GO_TO_THERAPIST)
            if not self.navigate_to_pose("therapist", self.pose_parameter("therapist_pose")):
                return

            self.transition(MissionState.WAIT_TASK)
            if not self.wait_for_task():
                return

            self.transition(MissionState.ACK_TASK)
            self.say(str(self.get_parameter("task_ack_text").value))

            self.transition(MissionState.WAIT_ACTIVITY)
            if not self.wait_for_activity():
                return

            self.transition(MissionState.ACK_ACTIVITY)
            self.say(str(self.get_parameter("activity_ack_text").value))

            self.transition(MissionState.WAIT_CHILD_NAME)
            if not self.wait_for_child_name():
                return

            self.transition(MissionState.ACK_CHILD_NAME)
            self.say(self.child_name_ack_text())

            self.transition(MissionState.GO_TO_SPAWN)
            if not self.navigate_to_pose("spawn", self.pose_parameter("spawn_pose")):
                return

            self.transition(MissionState.RUN_EMPOWER)
            if not self.run_empower(self._interaction_spec.task_description):
                return

            if bool(self.get_parameter("child_delivery_handled_by_empower").value):
                self.publish_feedback(
                    "Skipping hardcoded GO_TO_CHILD because EMPOWER handles child delivery"
                )
            else:
                self.transition(MissionState.GO_TO_CHILD)
                if not self.navigate_to_pose("child", self.pose_parameter("child_pose")):
                    return

            self.transition(MissionState.CHILD_READY)
            self.publish(self.child_ready_pub, "child_ready")
            self.publish_feedback("Child interaction can start")

            if not self.start_gaze_process():
                self.publish_feedback(
                    "Gaze estimation failed to start; aborting mission",
                    log_level="error",
                )
                self.transition(MissionState.MISSION_FAILED)
                return

            self.transition(MissionState.CHILD_ACTIVITY)
            self.run_child_activity()

            self.stop_gaze_process()

            self.transition(MissionState.MISSION_COMPLETE)
            self.publish_feedback("Mission complete")

        except Exception as exc:
            self.stop_gaze_process()
            self.publish_feedback(
                f"Mission failed with {type(exc).__name__}: {exc}",
                log_level="error",
            )

    def wait_for_task(self):
        timeout_sec = float(self.get_parameter("wait_for_task_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the physical task on /hri/user_utterance"
        )
        if self._task_event.wait(timeout=timeout_sec):
            return True
        self.publish_feedback(
            "Timed out waiting for a physical task from /hri/user_utterance",
            log_level="error",
        )
        return False

    def wait_for_child_name(self):
        timeout_sec = float(self.get_parameter("wait_for_child_name_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the child name on /hri/user_utterance"
        )
        if self._child_name_event.wait(timeout=timeout_sec):
            return True
        self.publish_feedback(
            "Timed out waiting for the child name from /hri/user_utterance",
            log_level="error",
        )
        return False

    def child_name_ack_text(self):
        template = str(self.get_parameter("child_name_ack_text").value)
        return template.format(child_name=self._interaction_spec.child_name)

    def wait_for_activity(self):
        timeout_sec = float(self.get_parameter("wait_for_activity_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the child activity on /hri/user_utterance"
        )
        if self._activity_event.wait(timeout=timeout_sec):
            return True
        self.publish_feedback(
            "Timed out waiting for a child activity from /hri/user_utterance",
            log_level="error",
        )
        return False

    def say(self, text):
        payload = json.dumps({"command": "say", "text": text})
        self.publish(self.dialog_command_pub, payload)
        self.publish_feedback(f"Requested robot speech: {text}")

    def run_child_activity(self):
        self._dialog_complete_event.clear()
        context = {
            "child_name": self._interaction_spec.child_name,
            "task": self._interaction_spec.task_description,
            "activity_description": self._interaction_spec.activity_description,
            "activity_type": self._interaction_spec.activity_type,
        }
        payload = json.dumps({"command": "start_child_activity", "context": context})
        self.publish(self.dialog_command_pub, payload)
        self.publish_feedback(f"Starting child activity: {payload}")

        timeout_sec = float(self.get_parameter("child_activity_timeout_sec").value)
        if timeout_sec <= 0:
            timeout_sec = float(self.get_parameter("storytelling_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.0f}s for child activity to complete"
        )
        completed = self._dialog_complete_event.wait(
            timeout=timeout_sec if timeout_sec > 0 else None
        )
        if completed:
            self.publish_feedback(
                f"Child activity complete: {self._dialog_complete_payload}"
            )
        else:
            self.publish_feedback("Child activity timed out; stopping HRI", log_level="warn")
            self.publish(self.dialog_command_pub, json.dumps({"command": "stop"}))

    def gaze_command_callback(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if payload.get("command") == "stop_gaze":
            self.publish_feedback("Gaze stop requested via /mission/gaze_command")
            self.stop_gaze_process()

    def gaze_node_status_callback(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        state = payload.get("state", "")
        if state == "READY":
            self._gaze_ready_event.set()
        elif state == "ERROR":
            self._gaze_error_message = payload.get("message", "unknown error")
            self._gaze_ready_event.set()

    def start_gaze_process(self):
        if not bool(self.get_parameter("gaze_estimation.enabled").value):
            self.publish_feedback("Gaze estimation disabled by parameter")
            return True

        camera_index = str(self.get_parameter("gaze_estimation.camera_index").value)
        device = str(self.get_parameter("gaze_estimation.device").value)
        show_window = str(self.get_parameter("gaze_estimation.show_window").value).lower()

        command = [
            "ros2", "launch", "gaze_estimation", "gaze_with_attention.launch.py",
            f"camera_index:={camera_index}",
            f"device:={device}",
            f"show_window:={show_window}",
        ]

        self._gaze_ready_event.clear()
        self._gaze_error_message = ""
        self._gaze_process, _ = self.start_process(command, self._gaze_process_label)

        timeout_sec = float(self.get_parameter("gaze_estimation.startup_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for gaze estimation to become ready"
        )

        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not self._stop_event.is_set():
            if self._gaze_ready_event.is_set():
                break
            if self._gaze_process.poll() is not None:
                self.publish_feedback(
                    f"Gaze estimation process exited early with code "
                    f"{self._gaze_process.returncode}",
                    log_level="error",
                )
                self._gaze_process = None
                return False
            if time.monotonic() >= deadline:
                self.publish_feedback(
                    "Gaze estimation startup timed out", log_level="error"
                )
                self.stop_gaze_process()
                return False
            rclpy.spin_once(self, timeout_sec=0.1)

        if self._gaze_error_message:
            self.publish_feedback(
                f"Gaze estimation failed to start: {self._gaze_error_message}",
                log_level="error",
            )
            self.stop_gaze_process()
            return False

        self.publish_feedback("Gaze estimation is ready")
        return True

    def stop_gaze_process(self):
        if self._gaze_process is not None:
            self.stop_process(self._gaze_process, self._gaze_process_label)
            self._gaze_process = None

    def normalize_activity_type(self, activity_description):
        text = activity_description.lower()
        rules = [
            (
                "computer_emotion_recognition",
                [
                    "computer emotion recognition",
                    "emotion recognition with the computer",
                    "use the computer for emotion recognition",
                    "show emotion pictures",
                    "recognize emotions from photos",
                    "recognize emotions from pictures",
                    "look at emotion photos",
                    "look at pictures of emotions",
                    "emotion recognition",
                    "recognize emotions",
                    "emotion pictures",
                    "emotion photos",
                    "emotions",
                ],
            ),
            (
                "mood_check_teddy",
                [
                    "how they feel",
                    "how he feels",
                    "how she feels",
                    "feelings",
                    "mood",
                    "state of mind",
                    "teddy",
                    "teddy bear",
                    "plush",
                ],
            ),
            (
                "storytelling",
                [
                    "story",
                    "storytelling",
                    "read the story",
                    "tell a story",
                ],
            ),
        ]
        for activity_type, keywords in rules:
            if any(keyword in text for keyword in keywords):
                return activity_type
        return str(self.get_parameter("default_activity_type").value)

    def navigate_to_pose(self, label, pose_values):
        pose = self.make_pose_stamped(pose_values)
        self.publish_feedback(
            f"Navigating to {label}: "
            f"x={pose.pose.position.x:.3f}, "
            f"y={pose.pose.position.y:.3f}, "
            f"yaw={pose_values[2]:.3f}"
        )
        if not self.nav_client.wait_for_server(
            timeout_sec=float(self.get_parameter("action_server_timeout_sec").value)
        ):
            self.publish_feedback(
                f"Nav2 action server '{self.get_parameter('nav_action_name').value}' is not available",
                log_level="error",
            )
            return False

        goal = NavigateToPose.Goal()
        goal.pose = pose
        send_future = self.nav_client.send_goal_async(goal)
        if not self.wait_for_future(
            send_future,
            float(self.get_parameter("action_server_timeout_sec").value),
            f"sending {label} Nav2 goal",
        ):
            return False

        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            self.publish_feedback(f"Nav2 rejected {label} goal", log_level="error")
            return False

        result_future = goal_handle.get_result_async()
        if not self.wait_for_future(
            result_future,
            float(self.get_parameter("nav_result_timeout_sec").value),
            f"waiting for {label} Nav2 result",
        ):
            goal_handle.cancel_goal_async()
            return False

        result = result_future.result()
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self.publish_feedback(f"Navigation to {label} succeeded")
            return True
        self.publish_feedback(
            f"Navigation to {label} failed with status {self.goal_status_name(result.status)}",
            log_level="error",
        )
        return False

    def run_empower(self, task_description):
        params_file = str(self.get_parameter("params_file").value)
        if not params_file:
            self.publish_feedback("params_file is empty; cannot run EMPOWER", log_level="error")
            return False
        if not os.path.exists(params_file):
            self.publish_feedback(
                f"params_file does not exist: {params_file}",
                log_level="error",
            )
            return False

        if bool(self.get_parameter("capture_before_empower").value):
            if not self.run_capture(params_file):
                return False

        if not self.run_checked(
            [
                "ros2",
                "launch",
                "empower",
                "empower_plan.launch.py",
                f"params_file:={params_file}",
                f"task_description:={task_description}",
            ],
            "EMPOWER planning",
        ):
            return False

        if not bool(self.get_parameter("execute_empower_plan").value):
            self.publish_feedback("EMPOWER plan execution disabled by parameter")
            return True
        return self.run_empower_execution(params_file)

    def run_capture(self, params_file):
        paths = self.capture_output_paths(params_file)
        capture_executable = (
            "multi_view_capture_node"
            if self.multi_view_enabled(params_file)
            else "scene_capture_node"
        )
        start_time = time.time()
        process, label = self.start_process(
            [
                "ros2",
                "run",
                "empower",
                capture_executable,
                "--ros-args",
                "--params-file",
                params_file,
                "-p",
                f"use_sim_time:={self.use_sim_time_value()}",
            ],
            "EMPOWER scene capture",
        )
        try:
            timeout_sec = float(self.get_parameter("capture_timeout_sec").value)
            deadline = time.monotonic() + timeout_sec
            while rclpy.ok() and not self._stop_event.is_set():
                if self.files_ready(paths, start_time):
                    self.publish_feedback("EMPOWER scene capture completed")
                    return True
                if process.poll() is not None:
                    if self.files_ready(paths, start_time):
                        self.publish_feedback("EMPOWER scene capture completed")
                        return True
                    self.publish_feedback(
                        f"{label} exited before capture files were ready "
                        f"(code {process.returncode})",
                        log_level="error",
                    )
                    return False
                if time.monotonic() >= deadline:
                    missing = [path for path in paths if not os.path.exists(path)]
                    self.publish_feedback(
                        "Timed out waiting for EMPOWER scene capture files: "
                        + ", ".join(missing or paths),
                        log_level="error",
                    )
                    return False
                time.sleep(0.2)
            return False
        finally:
            self.stop_process(process, label)

    def capture_output_paths(self, params_file):
        use_case = self.empower_param(params_file, "use_case", self.get_parameter("use_case").value)
        output_root = self.empower_param(
            params_file,
            "output_root",
            self.get_parameter("output_root").value,
        )
        if self.multi_view_enabled(params_file):
            output_subdir = self.empower_param(params_file, "multi_view.output_subdir", "views")
            return [
                os.path.join(output_root, "empower_output", use_case, "views_manifest.json"),
                os.path.join(output_root, "empower_output", use_case, output_subdir, "view_000", "rgb.jpg"),
                os.path.join(output_root, "empower_output", use_case, output_subdir, "view_000", "depth.npy"),
                os.path.join(output_root, "empower_output", use_case, output_subdir, "view_000", "camera_pose_map.json"),
            ]
        return [
            os.path.join(output_root, "empower_output", use_case, "scan.jpg"),
            os.path.join(output_root, "empower_output", use_case, "depth.npy"),
            os.path.join(output_root, "empower_output", use_case, "depth_pointcloud.pcd"),
            os.path.join(output_root, "empower_output", use_case, "camera_info.json"),
        ]

    def multi_view_enabled(self, params_file):
        return bool(self.empower_param(params_file, "multi_view.enabled", False))

    def empower_param(self, params_file, name, default):
        try:
            import yaml
        except ImportError:
            return default
        try:
            with open(params_file, "r", encoding="utf-8") as params_stream:
                data = yaml.safe_load(params_stream) or {}
        except OSError:
            return default
        for section in data.values():
            if not isinstance(section, dict):
                continue
            ros_parameters = section.get("ros__parameters", {})
            found, value = self.nested_param(ros_parameters, name)
            if found:
                return value
        return default

    def nested_param(self, ros_parameters, name):
        if name in ros_parameters:
            return True, ros_parameters[name]
        current = ros_parameters
        for part in str(name).split("."):
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current

    def files_ready(self, paths, start_time):
        for path in paths:
            if not os.path.exists(path):
                return False
            if os.path.getmtime(path) < start_time:
                return False
        return True

    def run_empower_execution(self, params_file):
        low_level_command = [
            "ros2",
            "run",
            "empower",
            "low_level_execution_node",
            "--ros-args",
            "--params-file",
            params_file,
            "-p",
            f"use_sim_time:={self.use_sim_time_value()}",
        ]
        if self.multi_view_enabled(params_file):
            self.publish_feedback(
                "Skipping single-view point cloud/marker publishers because multi-view "
                "execution uses global_detections.json"
            )
            return self.run_checked(low_level_command, "EMPOWER low-level execution")

        helpers = []
        try:
            helpers.append(
                self.start_process(
                    [
                        "ros2",
                        "run",
                        "empower",
                        "color_pcl_node",
                        "--ros-args",
                        "--params-file",
                        params_file,
                        "-p",
                        f"use_sim_time:={self.use_sim_time_value()}",
                    ],
                    "EMPOWER colored point cloud publisher",
                )
            )
            helpers.append(
                self.start_process(
                    [
                        "ros2",
                        "run",
                        "empower",
                        "marker_centroids_node",
                        "--ros-args",
                        "--params-file",
                        params_file,
                        "-p",
                        f"use_sim_time:={self.use_sim_time_value()}",
                    ],
                    "EMPOWER marker publisher",
                )
            )
            time.sleep(1.0)
            for process, label in helpers:
                if process.poll() is not None:
                    self.publish_feedback(
                        f"{label} exited early with code {process.returncode}",
                        log_level="error",
                    )
                    return False
            return self.run_checked(low_level_command, "EMPOWER low-level execution")
        finally:
            for process, label in reversed(helpers):
                self.stop_process(process, label)

    def start_process(self, command, label):
        self.publish_feedback(f"Starting {label}: {' '.join(command)}")
        return (
            subprocess.Popen(command, start_new_session=True),
            label,
        )

    def stop_process(self, process, label):
        if process.poll() is not None:
            return
        self.publish_feedback(f"Stopping {label}")
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5.0)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5.0)
            except ProcessLookupError:
                return

    def run_checked(self, command, label):
        self.publish_feedback(f"Running {label}: {' '.join(command)}")
        try:
            subprocess.run(
                command,
                check=True,
                timeout=self.process_timeout(),
            )
        except subprocess.TimeoutExpired:
            self.publish_feedback(f"{label} timed out", log_level="error")
            return False
        except subprocess.CalledProcessError as exc:
            self.publish_feedback(
                f"{label} failed with exit code {exc.returncode}",
                log_level="error",
            )
            return False
        self.publish_feedback(f"{label} completed")
        return True

    def process_timeout(self):
        timeout = float(self.get_parameter("empower_process_timeout_sec").value)
        return timeout if timeout > 0.0 else None

    def use_sim_time_value(self):
        return str(bool(self.get_parameter("use_sim_time").value)).lower()

    def wait_for_future(self, future, timeout_sec, description):
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not self._stop_event.is_set():
            if future.done():
                return True
            if time.monotonic() >= deadline:
                self.publish_feedback(f"Timed out while {description}", log_level="error")
                return False
            time.sleep(0.05)
        return False

    def pose_parameter(self, name):
        values = list(self.get_parameter(name).value)
        if len(values) != 3:
            raise ValueError(f"{name} must contain exactly [x, y, yaw]")
        return [float(values[0]), float(values[1]), float(values[2])]

    def make_pose_stamped(self, values):
        x, y, yaw = values
        qz = math.sin(yaw * 0.5)
        qw = math.cos(yaw * 0.5)
        pose = PoseStamped()
        pose.header.frame_id = str(self.get_parameter("map_frame").value)
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = 0.0
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw
        return pose

    def transition(self, state):
        with self._state_lock:
            self._state = state
        self.publish(self.state_pub, state.value)
        self.get_logger().info(f"Mission state: {state.value}")

    def current_state(self):
        with self._state_lock:
            return self._state

    def publish_feedback(self, text, log_level="info"):
        self.publish(self.feedback_pub, text)
        logger = self.get_logger()
        if log_level == "error":
            logger.error(text)
        elif log_level == "warn":
            logger.warn(text)
        elif log_level == "debug":
            logger.debug(text)
        else:
            logger.info(text)

    def publish(self, publisher, text):
        msg = String()
        msg.data = str(text)
        publisher.publish(msg)

    def goal_status_name(self, status):
        names = {
            GoalStatus.STATUS_UNKNOWN: "UNKNOWN",
            GoalStatus.STATUS_ACCEPTED: "ACCEPTED",
            GoalStatus.STATUS_EXECUTING: "EXECUTING",
            GoalStatus.STATUS_CANCELING: "CANCELING",
            GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
            GoalStatus.STATUS_CANCELED: "CANCELED",
            GoalStatus.STATUS_ABORTED: "ABORTED",
        }
        return names.get(status, str(status))

    def destroy_node(self):
        self._stop_event.set()
        self.stop_gaze_process()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MissionOrchestratorNode()
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

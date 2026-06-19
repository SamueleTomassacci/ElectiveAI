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


class MissionOrchestratorNode(Node):
    """Hardcoded MVP bridge between HRI, Nav2 and the EMPOWER ROS2 pipeline."""

    def __init__(self):
        super().__init__("mission_orchestrator_node")
        self._declare_parameters()

        status_qos = QoSProfile(depth=1)
        status_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.state_pub = self.create_publisher(String, "/mission/state", status_qos)
        self.feedback_pub = self.create_publisher(String, "/mission/feedback", 10)
        self.task_pub = self.create_publisher(String, "/mission/task_description", status_qos)
        self.child_ready_pub = self.create_publisher(String, "/mission/child_ready", status_qos)
        self.dialog_command_pub = self.create_publisher(String, "/hri/dialog_command", 10)

        self.create_subscription(String, "/hri/user_utterance", self._task_callback, 10)
        self.create_subscription(String, "/hri/dialog_complete", self._dialog_complete_callback, 10)
        self.create_subscription(String, "/mission/gaze_command", self._gaze_command_callback, 10)
        self.create_subscription(String, "/gaze/node_status", self._gaze_node_status_callback, 10)

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
                target=self._run_mission,
                name="hri_empower_mission",
                daemon=True,
            )
            self._mission_thread.start()
        else:
            self._publish_feedback("Mission orchestrator ready; auto_start is disabled")

    def _declare_parameters(self):
        self.declare_parameter("therapist_pose", [0.0, -1.6, -1.5708])
        self.declare_parameter("spawn_pose", [0.0, 0.0, 0.0])
        self.declare_parameter("child_pose", [0.0, 1.6, 1.5708])
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter(
            "params_file",
            "/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml",
        )
        self.declare_parameter("wait_for_task_timeout_sec", 120.0)
        self.declare_parameter("wait_for_activity_timeout_sec", 120.0)
        self.declare_parameter("task_ack_text", "Okay, I understood the task.")
        self.declare_parameter("activity_ack_text", "Okay, I understood the activity.")
        self.declare_parameter("default_activity_type", "generic_child_activity")
        self.declare_parameter("auto_start", True)
        self.declare_parameter("action_server_timeout_sec", 20.0)
        self.declare_parameter("nav_result_timeout_sec", 300.0)
        self.declare_parameter("empower_process_timeout_sec", 0.0)
        self.declare_parameter("capture_timeout_sec", 60.0)
        self.declare_parameter("capture_before_empower", True)
        self.declare_parameter("execute_empower_plan", True)
        self.declare_parameter("child_delivery_handled_by_empower", False)
        self.declare_parameter("use_case", "person_table_child_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        self.declare_parameter("storytelling_timeout_sec", 300.0)
        self.declare_parameter("child_activity_timeout_sec", 300.0)
        self.declare_parameter("gaze_estimation.enabled", True)
        self.declare_parameter("gaze_estimation.camera_index", 0)
        self.declare_parameter("gaze_estimation.device", "auto")
        self.declare_parameter("gaze_estimation.show_window", True)
        self.declare_parameter("gaze_estimation.startup_timeout_sec", 30.0)

    def _task_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        state = self._current_state()
        if state not in {MissionState.WAIT_TASK, MissionState.WAIT_ACTIVITY}:
            self._publish_feedback(
                f"Ignoring HRI utterance outside task/activity capture: {text}",
                log_level="debug",
            )
            return
        if state == MissionState.WAIT_TASK:
            self._capture_physical_task(text)
            return
        if state == MissionState.WAIT_ACTIVITY:
            self._capture_child_activity(text)

    def _capture_physical_task(self, text):
        if self._interaction_spec.task_description:
            self._publish_feedback(
                f"Ignoring extra HRI utterance after task capture: {text}",
                log_level="debug",
            )
            return
        self._interaction_spec.task_description = text
        self._publish(self.task_pub, text)
        self._publish_feedback(f"Received physical task: {text}")
        self._task_event.set()

    def _capture_child_activity(self, text):
        if self._interaction_spec.activity_description:
            self._publish_feedback(
                f"Ignoring extra HRI utterance after activity capture: {text}",
                log_level="debug",
            )
            return
        self._interaction_spec.activity_description = text
        self._interaction_spec.activity_type = self._normalize_activity_type(text)
        self._publish_feedback(f"Received child activity: {text}")
        self._publish_feedback(
            f"Normalized activity type: {self._interaction_spec.activity_type}"
        )
        self._activity_event.set()

    def _dialog_complete_callback(self, msg):
        self._dialog_complete_payload = msg.data
        self._dialog_complete_event.set()

    def _run_mission(self):
        try:
            self._transition(MissionState.GO_TO_THERAPIST)
            if not self._navigate_to_pose("therapist", self._pose_parameter("therapist_pose")):
                return

            self._transition(MissionState.WAIT_TASK)
            if not self._wait_for_task():
                return

            self._transition(MissionState.ACK_TASK)
            self._say(str(self.get_parameter("task_ack_text").value))

            self._transition(MissionState.WAIT_ACTIVITY)
            if not self._wait_for_activity():
                return

            self._transition(MissionState.ACK_ACTIVITY)
            self._say(str(self.get_parameter("activity_ack_text").value))

            self._transition(MissionState.GO_TO_SPAWN)
            if not self._navigate_to_pose("spawn", self._pose_parameter("spawn_pose")):
                return

            self._transition(MissionState.RUN_EMPOWER)
            if not self._run_empower(self._interaction_spec.task_description):
                return

            if bool(self.get_parameter("child_delivery_handled_by_empower").value):
                self._publish_feedback(
                    "Skipping hardcoded GO_TO_CHILD because EMPOWER handles child delivery"
                )
            else:
                self._transition(MissionState.GO_TO_CHILD)
                if not self._navigate_to_pose("child", self._pose_parameter("child_pose")):
                    return

            self._transition(MissionState.CHILD_READY)
            self._publish(self.child_ready_pub, "child_ready")
            self._publish_feedback("Child interaction can start")

            if not self._start_gaze_process():
                self._publish_feedback(
                    "Gaze estimation failed to start; aborting mission",
                    log_level="error",
                )
                self._transition(MissionState.MISSION_FAILED)
                return

            self._transition(MissionState.CHILD_ACTIVITY)
            self._run_child_activity()

            self._stop_gaze_process()

            self._transition(MissionState.MISSION_COMPLETE)
            self._publish_feedback("Mission complete")

        except Exception as exc:
            self._stop_gaze_process()
            self._publish_feedback(
                f"Mission failed with {type(exc).__name__}: {exc}",
                log_level="error",
            )

    def _wait_for_task(self):
        timeout_sec = float(self.get_parameter("wait_for_task_timeout_sec").value)
        self._publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the physical task on /hri/user_utterance"
        )
        if self._task_event.wait(timeout=timeout_sec):
            return True
        self._publish_feedback(
            "Timed out waiting for a physical task from /hri/user_utterance",
            log_level="error",
        )
        return False

    def _wait_for_activity(self):
        timeout_sec = float(self.get_parameter("wait_for_activity_timeout_sec").value)
        self._publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the child activity on /hri/user_utterance"
        )
        if self._activity_event.wait(timeout=timeout_sec):
            return True
        self._publish_feedback(
            "Timed out waiting for a child activity from /hri/user_utterance",
            log_level="error",
        )
        return False

    def _say(self, text):
        payload = json.dumps({"command": "say", "text": text})
        self._publish(self.dialog_command_pub, payload)
        self._publish_feedback(f"Requested robot speech: {text}")

    def _run_child_activity(self):
        self._dialog_complete_event.clear()
        context = {
            "task": self._interaction_spec.task_description,
            "activity_description": self._interaction_spec.activity_description,
            "activity_type": self._interaction_spec.activity_type,
        }
        payload = json.dumps({"command": "start_child_activity", "context": context})
        self._publish(self.dialog_command_pub, payload)
        self._publish_feedback(f"Starting child activity: {payload}")

        timeout_sec = float(self.get_parameter("child_activity_timeout_sec").value)
        if timeout_sec <= 0:
            timeout_sec = float(self.get_parameter("storytelling_timeout_sec").value)
        self._publish_feedback(
            f"Waiting up to {timeout_sec:.0f}s for child activity to complete"
        )
        completed = self._dialog_complete_event.wait(
            timeout=timeout_sec if timeout_sec > 0 else None
        )
        if completed:
            self._publish_feedback(
                f"Child activity complete: {self._dialog_complete_payload}"
            )
        else:
            self._publish_feedback("Child activity timed out; stopping HRI", log_level="warn")
            self._publish(self.dialog_command_pub, json.dumps({"command": "stop"}))

    def _gaze_command_callback(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if payload.get("command") == "stop_gaze":
            self._publish_feedback("Gaze stop requested via /mission/gaze_command")
            self._stop_gaze_process()

    def _gaze_node_status_callback(self, msg):
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

    def _start_gaze_process(self):
        if not bool(self.get_parameter("gaze_estimation.enabled").value):
            self._publish_feedback("Gaze estimation disabled by parameter")
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
        self._gaze_process, _ = self._start_process(command, self._gaze_process_label)

        timeout_sec = float(self.get_parameter("gaze_estimation.startup_timeout_sec").value)
        self._publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for gaze estimation to become ready"
        )

        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not self._stop_event.is_set():
            if self._gaze_ready_event.is_set():
                break
            if self._gaze_process.poll() is not None:
                self._publish_feedback(
                    f"Gaze estimation process exited early with code "
                    f"{self._gaze_process.returncode}",
                    log_level="error",
                )
                self._gaze_process = None
                return False
            if time.monotonic() >= deadline:
                self._publish_feedback(
                    "Gaze estimation startup timed out", log_level="error"
                )
                self._stop_gaze_process()
                return False
            rclpy.spin_once(self, timeout_sec=0.1)

        if self._gaze_error_message:
            self._publish_feedback(
                f"Gaze estimation failed to start: {self._gaze_error_message}",
                log_level="error",
            )
            self._stop_gaze_process()
            return False

        self._publish_feedback("Gaze estimation is ready")
        return True

    def _stop_gaze_process(self):
        if self._gaze_process is not None:
            self._stop_process(self._gaze_process, self._gaze_process_label)
            self._gaze_process = None

    def _normalize_activity_type(self, activity_description):
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

    def _navigate_to_pose(self, label, pose_values):
        pose = self._make_pose_stamped(pose_values)
        self._publish_feedback(
            f"Navigating to {label}: "
            f"x={pose.pose.position.x:.3f}, "
            f"y={pose.pose.position.y:.3f}, "
            f"yaw={pose_values[2]:.3f}"
        )
        if not self.nav_client.wait_for_server(
            timeout_sec=float(self.get_parameter("action_server_timeout_sec").value)
        ):
            self._publish_feedback(
                f"Nav2 action server '{self.get_parameter('nav_action_name').value}' is not available",
                log_level="error",
            )
            return False

        goal = NavigateToPose.Goal()
        goal.pose = pose
        send_future = self.nav_client.send_goal_async(goal)
        if not self._wait_for_future(
            send_future,
            float(self.get_parameter("action_server_timeout_sec").value),
            f"sending {label} Nav2 goal",
        ):
            return False

        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            self._publish_feedback(f"Nav2 rejected {label} goal", log_level="error")
            return False

        result_future = goal_handle.get_result_async()
        if not self._wait_for_future(
            result_future,
            float(self.get_parameter("nav_result_timeout_sec").value),
            f"waiting for {label} Nav2 result",
        ):
            goal_handle.cancel_goal_async()
            return False

        result = result_future.result()
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self._publish_feedback(f"Navigation to {label} succeeded")
            return True
        self._publish_feedback(
            f"Navigation to {label} failed with status {self._goal_status_name(result.status)}",
            log_level="error",
        )
        return False

    def _run_empower(self, task_description):
        params_file = str(self.get_parameter("params_file").value)
        if not params_file:
            self._publish_feedback("params_file is empty; cannot run EMPOWER", log_level="error")
            return False
        if not os.path.exists(params_file):
            self._publish_feedback(
                f"params_file does not exist: {params_file}",
                log_level="error",
            )
            return False

        if bool(self.get_parameter("capture_before_empower").value):
            if not self._run_capture(params_file):
                return False

        if not self._run_checked(
            [
                "ros2",
                "launch",
                "empower_ros2",
                "empower_plan.launch.py",
                f"params_file:={params_file}",
                f"task_description:={task_description}",
            ],
            "EMPOWER planning",
        ):
            return False

        if not bool(self.get_parameter("execute_empower_plan").value):
            self._publish_feedback("EMPOWER plan execution disabled by parameter")
            return True
        return self._run_empower_execution(params_file)

    def _run_capture(self, params_file):
        paths = self._capture_output_paths(params_file)
        start_time = time.time()
        process, label = self._start_process(
            [
                "ros2",
                "run",
                "empower_ros2",
                "scene_capture_node",
                "--ros-args",
                "--params-file",
                params_file,
            ],
            "EMPOWER scene capture",
        )
        try:
            timeout_sec = float(self.get_parameter("capture_timeout_sec").value)
            deadline = time.monotonic() + timeout_sec
            while rclpy.ok() and not self._stop_event.is_set():
                if self._files_ready(paths, start_time):
                    self._publish_feedback("EMPOWER scene capture completed")
                    return True
                if process.poll() is not None:
                    if self._files_ready(paths, start_time):
                        self._publish_feedback("EMPOWER scene capture completed")
                        return True
                    self._publish_feedback(
                        f"{label} exited before capture files were ready "
                        f"(code {process.returncode})",
                        log_level="error",
                    )
                    return False
                if time.monotonic() >= deadline:
                    missing = [path for path in paths if not os.path.exists(path)]
                    self._publish_feedback(
                        "Timed out waiting for EMPOWER scene capture files: "
                        + ", ".join(missing or paths),
                        log_level="error",
                    )
                    return False
                time.sleep(0.2)
            return False
        finally:
            self._stop_process(process, label)

    def _capture_output_paths(self, params_file):
        use_case = self._empower_param(params_file, "use_case", self.get_parameter("use_case").value)
        output_root = self._empower_param(
            params_file,
            "output_root",
            self.get_parameter("output_root").value,
        )
        return [
            os.path.join(output_root, "images", use_case, "scan.jpg"),
            os.path.join(output_root, "output", use_case, "depth.npy"),
            os.path.join(output_root, "output", use_case, "depth_pointcloud.pcd"),
            os.path.join(output_root, "output", use_case, "camera_info.json"),
        ]

    def _empower_param(self, params_file, name, default):
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
            if name in ros_parameters:
                return ros_parameters[name]
        return default

    def _files_ready(self, paths, start_time):
        for path in paths:
            if not os.path.exists(path):
                return False
            if os.path.getmtime(path) < start_time:
                return False
        return True

    def _run_empower_execution(self, params_file):
        helpers = []
        try:
            helpers.append(
                self._start_process(
                    [
                        "ros2",
                        "run",
                        "empower_ros2",
                        "color_pcl_node",
                        "--ros-args",
                        "--params-file",
                        params_file,
                    ],
                    "EMPOWER colored point cloud publisher",
                )
            )
            helpers.append(
                self._start_process(
                    [
                        "ros2",
                        "run",
                        "empower_ros2",
                        "marker_centroids_node",
                        "--ros-args",
                        "--params-file",
                        params_file,
                    ],
                    "EMPOWER marker publisher",
                )
            )
            time.sleep(1.0)
            for process, label in helpers:
                if process.poll() is not None:
                    self._publish_feedback(
                        f"{label} exited early with code {process.returncode}",
                        log_level="error",
                    )
                    return False
            return self._run_checked(
                [
                    "ros2",
                    "run",
                    "empower_ros2",
                    "low_level_execution_node",
                    "--ros-args",
                    "--params-file",
                    params_file,
                ],
                "EMPOWER low-level execution",
            )
        finally:
            for process, label in reversed(helpers):
                self._stop_process(process, label)

    def _start_process(self, command, label):
        self._publish_feedback(f"Starting {label}: {' '.join(command)}")
        return (
            subprocess.Popen(command, start_new_session=True),
            label,
        )

    def _stop_process(self, process, label):
        if process.poll() is not None:
            return
        self._publish_feedback(f"Stopping {label}")
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

    def _run_checked(self, command, label):
        self._publish_feedback(f"Running {label}: {' '.join(command)}")
        try:
            subprocess.run(
                command,
                check=True,
                timeout=self._process_timeout(),
            )
        except subprocess.TimeoutExpired:
            self._publish_feedback(f"{label} timed out", log_level="error")
            return False
        except subprocess.CalledProcessError as exc:
            self._publish_feedback(
                f"{label} failed with exit code {exc.returncode}",
                log_level="error",
            )
            return False
        self._publish_feedback(f"{label} completed")
        return True

    def _process_timeout(self):
        timeout = float(self.get_parameter("empower_process_timeout_sec").value)
        return timeout if timeout > 0.0 else None

    def _wait_for_future(self, future, timeout_sec, description):
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not self._stop_event.is_set():
            if future.done():
                return True
            if time.monotonic() >= deadline:
                self._publish_feedback(f"Timed out while {description}", log_level="error")
                return False
            time.sleep(0.05)
        return False

    def _pose_parameter(self, name):
        values = list(self.get_parameter(name).value)
        if len(values) != 3:
            raise ValueError(f"{name} must contain exactly [x, y, yaw]")
        return [float(values[0]), float(values[1]), float(values[2])]

    def _make_pose_stamped(self, values):
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

    def _transition(self, state):
        with self._state_lock:
            self._state = state
        self._publish(self.state_pub, state.value)
        self.get_logger().info(f"Mission state: {state.value}")

    def _current_state(self):
        with self._state_lock:
            return self._state

    def _publish_feedback(self, text, log_level="info"):
        self._publish(self.feedback_pub, text)
        logger = self.get_logger()
        if log_level == "error":
            logger.error(text)
        elif log_level == "warn":
            logger.warn(text)
        elif log_level == "debug":
            logger.debug(text)
        else:
            logger.info(text)

    def _publish(self, publisher, text):
        msg = String()
        msg.data = str(text)
        publisher.publish(msg)

    def _goal_status_name(self, status):
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
        self._stop_gaze_process()
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

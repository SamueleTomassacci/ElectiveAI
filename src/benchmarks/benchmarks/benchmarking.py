
import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory

from empower.static_scene_graph import StaticSceneGraph, default_scene_graph_path


DEFAULT_CONFIG = "/root/exchange/src/benchmarks/config/benchmarks.yaml"
DEFAULT_EMPOWER_OUTPUT = "/root/exchange/output/empower_output/therapy_task"


class BenchmarkPaths:
    def __init__(
        self,
        run_dir,
        records_path,
        summary_path,
        markdown_path,
    ):
        self.run_dir = run_dir
        self.records_path = records_path
        self.summary_path = summary_path
        self.markdown_path = markdown_path


class BenchmarkRecorder:
    def __init__(self, output_root, run_id):
        self.paths = self._make_paths(output_root, run_id)
        self.records = []

    def _make_paths(self, output_root, run_id):
        run_dir = Path(output_root).expanduser() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return BenchmarkPaths(
            run_dir=run_dir,
            records_path=run_dir / "results.jsonl",
            summary_path=run_dir / "summary.json",
            markdown_path=run_dir / "summary.md",
        )

    def record(self, payload):
        payload = dict(payload)
        payload.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        self.records.append(payload)
        with self.paths.records_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def write_summary(self):
        summary = summarize_records(self.records)
        with self.paths.summary_path.open("w", encoding="utf-8") as stream:
            json.dump(summary, stream, indent=2, ensure_ascii=False)
        self.paths.markdown_path.write_text(render_markdown(summary), encoding="utf-8")
        return summary


def load_config(path):
    if path == DEFAULT_CONFIG and not os.path.exists(path):
        repo_candidate = Path(__file__).resolve().parents[1] / "config" / "benchmarks.yaml"
        if repo_candidate.exists():
            path = str(repo_candidate)
        else:
            try:
                share_candidate = (
                    Path(get_package_share_directory("benchmarks"))
                    / "config"
                    / "benchmarks.yaml"
                )
                if share_candidate.exists():
                    path = str(share_candidate)
            except PackageNotFoundError:
                pass
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def nested_get(data, dotted, default = None):
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def normalize_label(value):
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def label_matches(found, aliases):
    found_norm = normalize_label(found)
    for alias in aliases:
        alias_norm = normalize_label(alias)
        if alias_norm and (alias_norm in found_norm or found_norm in alias_norm):
            return True
    return False


def now_id(prefix):
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return default


def read_text(path, default = ""):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


def keyword_accuracy(text, keywords):
    keys = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if not keys:
        return 1.0
    normalized = normalize_label(text)
    hits = sum(1 for keyword in keys if normalize_label(keyword) in normalized)
    return hits / len(keys)


def run_hri_functionality(config, recorder):
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class HriE2eBenchmarkNode(Node):
        def __init__(self):
            super().__init__("therapy_hri_benchmark")
            self.user_utterances = []
            self.robot_utterances = []
            self.states = []
            self.create_subscription(String, "/hri/user_utterance", self._user_cb, 10)
            self.create_subscription(String, "/hri/robot_utterance", self._robot_cb, 10)
            self.create_subscription(String, "/hri/dialog_state", self._state_cb, 10)

        def _user_cb(self, msg):
            self.user_utterances.append(
                {"text": msg.data, "_received_monotonic": time.monotonic()}
            )

        def _robot_cb(self, msg):
            self.robot_utterances.append(
                {"text": msg.data, "_received_monotonic": time.monotonic()}
            )

        def _state_cb(self, msg):
            self.states.append(
                {"state": msg.data, "_received_monotonic": time.monotonic()}
            )

        def wait_for_voice_node(self, timeout_sec):
            deadline = time.monotonic() + timeout_sec
            details = {}
            while time.monotonic() < deadline:
                details = {
                    "user_utterance_publishers": self.count_publishers("/hri/user_utterance"),
                    "robot_utterance_publishers": self.count_publishers("/hri/robot_utterance"),
                    "dialog_state_publishers": self.count_publishers("/hri/dialog_state"),
                }
                if all(value > 0 for value in details.values()):
                    details["ready"] = True
                    return details
                rclpy.spin_once(self, timeout_sec=0.1)
                time.sleep(0.1)
            details["ready"] = False
            return details

    rclpy.init(args=None)
    node = HriE2eBenchmarkNode()
    hri_process = None
    try:
        hri_cfg = nested_get(config, "functionality.hri", {})
        startup_timeout = float(hri_cfg.get("startup_timeout_sec", 60.0))
        max_turn_duration = float(hri_cfg.get("max_turn_duration_sec", 45.0))
        start_node = bool(hri_cfg.get("start_node", True))

        if start_node:
            for _ in range(10):
                rclpy.spin_once(node, timeout_sec=0.1)
            if node.count_publishers("/hri/user_utterance") == 0:
                command = [
                    "ros2",
                    "run",
                    "llm_interaction",
                    "voice_llm_node",
                    "--ros-args",
                    "--params-file",
                    str(hri_cfg.get("hri_params_file", "/root/exchange/src/llm_interaction/config/llm_config.yaml")),
                    "-p",
                    "input_mode:=voice",
                    "-p",
                    "therapist_llm_replies:=true",
                    "-p",
                    "follow_mission_state_for_therapist_input:=false",
                    "-p",
                    f"record_seconds:={float(hri_cfg.get('record_seconds', 5.0))}",
                ]
                hri_process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    start_new_session=True,
                    text=True,
                )

        details = node.wait_for_voice_node(startup_timeout)
        if not details.get("ready"):
            recorder.record(
                {
                    "type": "functionality",
                    "module": "hri",
                    "case": "voice_node_startup",
                    "success": False,
                    "details": details,
                    "error": "voice_llm_node was not discovered on required topics",
                }
            )
            recorder.record(
                {
                    "type": "functionality_summary",
                    "module": "hri",
                    "success": False,
                    "success_rate": 0.0,
                }
            )
            return

        recorder.record(
            {
                "type": "diagnostic",
                "module": "hri",
                "case": "voice_node_startup",
                "success": True,
                "details": details,
            }
        )

        cases = list(hri_cfg.get("cases", []))
        prompt_before_each_case = bool(hri_cfg.get("prompt_before_each_case", True))
        manual_response_rating = bool(hri_cfg.get("manual_response_rating", True))
        require_response_rating = bool(hri_cfg.get("require_response_rating", manual_response_rating))
        min_response_rating = float(hri_cfg.get("min_response_rating", 6.0))
        results = []
        for index, case in enumerate(cases, start=1):
            case_id = str(case.get("id", f"case_{index}"))
            prompt = str(case.get("prompt", ""))
            print("")
            print(f"[HRI E2E benchmark] Case {index}/{len(cases)}: {case_id}")
            print(f"Say aloud: {prompt}")
            if prompt_before_each_case and sys.stdin.isatty():
                input("Press ENTER when you are ready; recording will start immediately...")
            elif prompt_before_each_case:
                print("Recording will start in 3 seconds...")
                time.sleep(3.0)

            start_time = time.monotonic()
            if hri_process is not None and hri_process.stdin is not None:
                try:
                    hri_process.stdin.write("\n")
                    hri_process.stdin.flush()
                except BrokenPipeError:
                    pass
            else:
                print("Trigger recording manually in the voice_llm_node terminal now.")

            deadline = start_time + max_turn_duration
            observed_user = None
            observed_robot = None
            observed_ready = None
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                if observed_user is None:
                    observed_user = first_after(node.user_utterances, start_time)
                if observed_user is not None and observed_robot is None:
                    observed_robot = first_after(
                        node.robot_utterances,
                        observed_user["_received_monotonic"],
                    )
                if observed_robot is not None and observed_ready is None:
                    observed_ready = first_state_after(
                        node.states,
                        observed_robot["_received_monotonic"],
                        "ready",
                    )
                if (
                    observed_user is not None
                    and observed_robot is not None
                    and observed_ready is not None
                ):
                    break
                time.sleep(0.05)

            response_rating = None
            if manual_response_rating and observed_robot is not None:
                response_rating = ask_response_rating(
                    case_id=case_id,
                    robot_text=str(observed_robot.get("text", "")),
                )

            result = evaluate_hri_case(
                case=case,
                observed_user=observed_user,
                observed_robot=observed_robot,
                observed_ready=observed_ready,
                response_rating=response_rating,
                require_response_rating=require_response_rating,
                min_response_rating=min_response_rating,
                start_time=start_time,
                deadline=deadline,
            )
            results.append(result)
            recorder.record(result)

        successes = sum(1 for result in results if result.get("success"))
        recorder.record(
            {
                "type": "functionality_summary",
                "module": "hri",
                "success": successes == len(results) and bool(results),
                "success_rate": round(successes / len(results), 3) if results else 0.0,
            }
        )
    finally:
        if hri_process is not None:
            stop_hri_process(hri_process)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def first_after(items, start_time):
    for item in items:
        if item.get("_received_monotonic", 0.0) >= start_time:
            return item
    return None


def first_state_after(
    items, start_time, state
):
    for item in items:
        if (
            item.get("_received_monotonic", 0.0) >= start_time
            and item.get("state") == state
        ):
            return item
    return None


def ask_response_rating(
    case_id,
    robot_text,
    benchmark_label = "HRI E2E benchmark",
):
    print("")
    print("=" * 72)
    print(f"[{benchmark_label}] Robot response for {case_id}:")
    print(robot_text or "(empty response)")
    print("=" * 72)
    if not sys.stdin.isatty():
        return None
    while True:
        value = input("Enter response quality rating from 1 to 10 (empty = skip): ").strip()
        if not value:
            return None
        try:
            rating = float(value)
        except ValueError:
            print("Please enter a number from 1 to 10.")
            continue
        if 1.0 <= rating <= 10.0:
            return rating
        print("Please enter a number from 1 to 10.")


def evaluate_hri_case(
    *,
    case,
    observed_user,
    observed_robot,
    observed_ready,
    response_rating,
    require_response_rating,
    min_response_rating,
    start_time,
    deadline,
):
    case_id = str(case.get("id", "case"))
    user_text = str(observed_user.get("text", "")) if observed_user else ""
    robot_text = str(observed_robot.get("text", "")) if observed_robot else ""
    stt_accuracy = keyword_accuracy(user_text, case.get("expected_stt_keywords", []))
    response_received = observed_robot is not None and bool(robot_text.strip())
    turn_completed = observed_ready is not None
    turn_latency = (
        observed_ready["_received_monotonic"] - start_time
        if observed_ready is not None
        else None
    )
    max_latency = float(case.get("max_latency_sec", deadline - start_time))
    latency_ok = turn_latency is not None and turn_latency <= max_latency
    if response_rating is not None:
        rating_ok = response_rating >= min_response_rating
    else:
        rating_ok = response_received and not require_response_rating
    success = (
        bool(user_text.strip())
        and response_received
        and turn_completed
        and stt_accuracy >= float(case.get("stt_accuracy_threshold", 0.5))
        and rating_ok
        and latency_ok
    )
    return {
        "type": "functionality",
        "module": "hri",
        "case": case_id,
        "success": success,
        "prompt": case.get("prompt", ""),
        "recognized_text": user_text,
        "robot_response": robot_text,
        "stt_keyword_accuracy": round(stt_accuracy, 3),
        "response_rating": round(response_rating, 1) if response_rating is not None else None,
        "response_rating_required": require_response_rating,
        "min_response_rating": min_response_rating,
        "response_received": response_received,
        "turn_completed": turn_completed,
        "turn_latency_sec": round(turn_latency, 3)
        if turn_latency is not None
        else None,
    }


def run_perception_functionality(config, recorder):
    dump_dir = Path(DEFAULT_EMPOWER_OUTPUT)
    detections = read_json(dump_dir / "detections.json", {})
    global_detections = read_json(dump_dir / "global_detections.json", [])
    global_scene_graph = read_json(dump_dir / "global_scene_graph.json", {})
    labels = artifact_labels(detections, global_detections, global_scene_graph)
    artifact_files = sorted(path.name for path in dump_dir.iterdir()) if dump_dir.exists() else []

    expected = nested_get(config, "functionality.perception.expected_objects", [])
    matched = 0
    for item in expected:
        aliases = [item["label"], *item.get("aliases", [])]
        found = [label for label in labels if label_matches(label, aliases)]
        ok = bool(found)
        matched += int(ok)
        recorder.record(
            {
                "type": "functionality",
                "module": "empower_perception",
                "case": item["label"],
                "success": ok,
                "observed_labels": found,
                "all_observed_labels": labels,
                "source_dir": str(dump_dir),
                "artifact_files": artifact_files,
            }
        )

    total = len(expected)
    recorder.record(
        {
            "type": "functionality_summary",
            "module": "empower_perception",
            "success": matched == total and total > 0,
            "accuracy": round(matched / total, 3) if total else 0.0,
            "observed_label_count": len(labels),
        }
    )


def yaw_from_quaternion(z, w):
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


def angle_error(a, b):
    return abs(math.atan2(math.sin(a - b), math.cos(a - b)))


def run_nav2_functionality(config, recorder):
    import rclpy
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped
    from nav2_msgs.action import NavigateToPose
    from nav_msgs.msg import Odometry
    from rclpy.action import ActionClient
    from rclpy.node import Node

    class NavBenchmarkNode(Node):
        def __init__(self):
            super().__init__("therapy_nav2_benchmark")
            self.latest_odom = None
            self.create_subscription(Odometry, "/ground_truth_odom", self._odom_cb, 10)
            self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        def _odom_cb(self, msg):
            self.latest_odom = msg

    rclpy.init(args=None)
    node = NavBenchmarkNode()
    try:
        graph_path = default_scene_graph_path()
        if not os.path.exists(graph_path):
            graph_path = "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
        graph = StaticSceneGraph.from_json(graph_path)
        action_timeout = float(nested_get(config, "functionality.nav2.action_server_timeout_sec", 20.0))
        result_timeout = float(nested_get(config, "functionality.nav2.result_timeout_sec", 300.0))
        tolerance = float(nested_get(config, "functionality.nav2.position_tolerance_m", 0.35))
        if not node.client.wait_for_server(timeout_sec=action_timeout):
            recorder.record(
                {
                    "type": "functionality_summary",
                    "module": "nav2",
                    "success": False,
                    "error": "NavigateToPose action server is not available",
                }
            )
            return

        successes = 0
        goals = nested_get(config, "functionality.nav2.goals", [])
        for goal_cfg in goals:
            resolved = graph.resolve_nav_pose(goal_cfg["target"], role=goal_cfg.get("role"))
            if resolved is None:
                recorder.record(
                    {
                        "type": "functionality",
                        "module": "nav2",
                        "case": goal_cfg["id"],
                        "success": False,
                        "error": "static scene graph pose not found",
                    }
                )
                continue

            pose = PoseStamped()
            pose.header.frame_id = graph.frame_id
            pose.header.stamp = node.get_clock().now().to_msg()
            pose.pose.position.x = float(resolved["x"])
            pose.pose.position.y = float(resolved["y"])
            yaw = float(resolved["yaw"])
            pose.pose.orientation.z = math.sin(yaw * 0.5)
            pose.pose.orientation.w = math.cos(yaw * 0.5)

            request = NavigateToPose.Goal()
            request.pose = pose
            started = time.monotonic()
            send_future = node.client.send_goal_async(request)
            rclpy.spin_until_future_complete(node, send_future, timeout_sec=action_timeout)
            if not send_future.done() or send_future.result() is None:
                ok = False
                status_name = "SEND_TIMEOUT"
            else:
                handle = send_future.result()
                if not handle.accepted:
                    ok = False
                    status_name = "REJECTED"
                else:
                    result_future = handle.get_result_async()
                    rclpy.spin_until_future_complete(node, result_future, timeout_sec=result_timeout)
                    if not result_future.done():
                        handle.cancel_goal_async()
                        ok = False
                        status_name = "RESULT_TIMEOUT"
                    else:
                        status = result_future.result().status
                        ok = status == GoalStatus.STATUS_SUCCEEDED
                        status_name = goal_status_name(status)
            duration = time.monotonic() - started

            for _ in range(10):
                rclpy.spin_once(node, timeout_sec=0.05)
            position_error = None
            yaw_error = None
            if node.latest_odom is not None:
                odom_pose = node.latest_odom.pose.pose
                position_error = math.hypot(
                    odom_pose.position.x - pose.pose.position.x,
                    odom_pose.position.y - pose.pose.position.y,
                )
                yaw_error = angle_error(
                    yaw_from_quaternion(
                        odom_pose.orientation.z,
                        odom_pose.orientation.w,
                    ),
                    yaw,
                )

            final_ok = ok and (position_error is None or position_error <= tolerance)
            successes += int(final_ok)
            recorder.record(
                {
                    "type": "functionality",
                    "module": "nav2",
                    "case": goal_cfg["id"],
                    "success": final_ok,
                    "nav_status": status_name,
                    "duration_sec": round(duration, 3),
                    "target": {"x": resolved["x"], "y": resolved["y"], "yaw": resolved["yaw"]},
                    "position_error_m": round(position_error, 3) if position_error is not None else None,
                    "yaw_error_rad": round(yaw_error, 3) if yaw_error is not None else None,
                }
            )

        total = len(goals)
        recorder.record(
            {
                "type": "functionality_summary",
                "module": "nav2",
                "success": successes == total and total > 0,
                "success_rate": round(successes / total, 3) if total else 0.0,
            }
        )
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def goal_status_name(status):
    try:
        from action_msgs.msg import GoalStatus
    except ImportError:
        return str(status)
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


def run_gaze_functionality(config, recorder):
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class LiveGazeBenchmarkNode(Node):
        def __init__(self):
            super().__init__("therapy_gaze_benchmark")
            self.gaze_samples = []
            self.alerts = []
            self.node_statuses = []
            self.emotion_samples = []
            self.command_pub = self.create_publisher(String, "/hri/dialog_command", 10)
            self.state_pub = self.create_publisher(String, "/hri/dialog_state", 10)
            self.create_subscription(String, "/gaze/status", self._gaze_cb, 10)
            self.create_subscription(String, "/hri/attention_alert", self._alert_cb, 10)
            self.create_subscription(String, "/gaze/node_status", self._node_status_cb, 10)
            self.create_subscription(String, "/child/emotion_status", self._emotion_cb, 10)

        def _gaze_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.gaze_samples.append(payload)

        def _alert_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.alerts.append(payload)

        def _node_status_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.node_statuses.append(payload)

        def _emotion_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.emotion_samples.append(payload)

        def publish_json(self, publisher, payload):
            msg = String()
            msg.data = json.dumps(payload)
            publisher.publish(msg)

        def publish_text(self, publisher, text):
            msg = String()
            msg.data = text
            publisher.publish(msg)

        def wait_for_pipeline(self, timeout_sec):
            deadline = time.monotonic() + timeout_sec
            details = {}
            while time.monotonic() < deadline:
                details = {
                    "gaze_status_publishers": self.count_publishers("/gaze/status"),
                    "gaze_node_status_publishers": self.count_publishers("/gaze/node_status"),
                    "dialog_command_subscribers": self.count_subscribers("/hri/dialog_command"),
                    "dialog_state_subscribers": self.count_subscribers("/hri/dialog_state"),
                    "attention_alert_publishers": self.count_publishers("/hri/attention_alert"),
                }
                ready_status = any(
                    item.get("state") == "READY" for item in self.node_statuses
                )
                details["gaze_ready_status_received"] = ready_status
                if all(value > 0 for value in details.values() if isinstance(value, int)) and ready_status:
                    details["ready"] = True
                    return details
                rclpy.spin_once(self, timeout_sec=0.1)
                time.sleep(0.1)
            details["ready"] = False
            return details

    rclpy.init(args=None)
    node = LiveGazeBenchmarkNode()
    gaze_process = None
    try:
        gaze_cfg = nested_get(config, "functionality.gaze", {})
        if not gaze_cfg:
            gaze_cfg = nested_get(config, "functionality.gaze_attention", {})

        startup_timeout = float(gaze_cfg.get("startup_timeout_sec", 120.0))
        start_launch = bool(gaze_cfg.get("start_launch", True))
        if start_launch:
            for _ in range(10):
                rclpy.spin_once(node, timeout_sec=0.1)
            if node.count_publishers("/gaze/status") == 0:
                target_classes = ",".join(str(item) for item in gaze_cfg.get("target_classes", []))
                command = [
                    "ros2",
                    "launch",
                    "gaze_estimation",
                    "gaze_with_attention.launch.py",
                    f"camera_index:={gaze_cfg.get('camera_index', 0)}",
                    f"device:={gaze_cfg.get('device', 'auto')}",
                    f"show_window:={str(bool(gaze_cfg.get('show_window', True))).lower()}",
                ]
                if target_classes:
                    command.append(f"target_classes:={target_classes}")
                gaze_process = subprocess.Popen(command, start_new_session=True)

        pipeline_details = node.wait_for_pipeline(startup_timeout)
        if not pipeline_details.get("ready"):
            recorder.record(
                {
                    "type": "functionality",
                    "module": "gaze",
                    "case": "gaze_pipeline_startup",
                    "success": False,
                    "details": pipeline_details,
                    "error": "live gaze pipeline was not discovered or did not report READY",
                }
            )
            recorder.record(
                {
                    "type": "functionality_summary",
                    "module": "gaze",
                    "success": False,
                    "success_rate": 0.0,
                }
            )
            return

        recorder.record(
            {
                "type": "diagnostic",
                "module": "gaze",
                "case": "gaze_pipeline_startup",
                "success": True,
                "details": pipeline_details,
            }
        )

        phases = list(gaze_cfg.get("phases", []))
        default_duration = float(gaze_cfg.get("phase_duration_sec", 10.0))
        prompt_before_each_phase = bool(gaze_cfg.get("prompt_before_each_phase", True))
        phase_results = []

        for index, phase in enumerate(phases, start=1):
            phase_id = str(phase.get("id", f"phase_{index}"))
            instruction = str(phase.get("instruction", phase_id))
            duration = float(phase.get("duration_sec", default_duration))
            activity_type = str(phase.get("activity_type", "storytelling"))
            expected_alert = bool(phase.get("expected_alert", False))

            print("")
            print(f"[gaze live benchmark] Phase {index}/{len(phases)}: {phase_id}")
            print(instruction)
            print(f"Duration: {duration:.1f}s")
            if prompt_before_each_phase and sys.stdin.isatty():
                input("Press ENTER when ready...")
            elif prompt_before_each_phase:
                print("Starting in 3 seconds...")
                time.sleep(3.0)

            node.publish_json(
                node.command_pub,
                {
                    "command": "start_child_activity",
                    "context": {"activity_type": activity_type},
                },
            )
            phase_start = time.monotonic()
            phase_end = phase_start + duration
            while time.monotonic() < phase_end:
                node.publish_text(node.state_pub, "speaking")
                rclpy.spin_once(node, timeout_sec=0.1)
                time.sleep(0.05)

            grace_end = time.monotonic() + 0.5
            while time.monotonic() < grace_end:
                rclpy.spin_once(node, timeout_sec=0.1)
                time.sleep(0.05)

            result = evaluate_live_gaze_phase(
                phase=phase,
                samples=[
                    sample for sample in node.gaze_samples
                    if phase_start <= sample.get("_received_monotonic", 0.0) <= phase_end
                ],
                alerts=[
                    alert for alert in node.alerts
                    if phase_start <= alert.get("_received_monotonic", 0.0) <= phase_end + 0.5
                ],
                start_time=phase_start,
            )
            phase_results.append(result)
            recorder.record(result)

        node.publish_text(node.state_pub, "idle")
        node.publish_json(node.command_pub, {"command": "stop"})
        successes = sum(1 for item in phase_results if item.get("success"))
        recorder.record(
            {
                "type": "functionality_summary",
                "module": "gaze",
                "success": successes == len(phase_results) and bool(phase_results),
                "success_rate": round(successes / len(phase_results), 3)
                if phase_results
                else 0.0,
            }
        )
    finally:
        if gaze_process is not None:
            stop_process(gaze_process)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def evaluate_live_gaze_phase(
    *,
    phase,
    samples,
    alerts,
    start_time,
):
    phase_id = str(phase.get("id", "phase"))
    expected_statuses = phase.get("expected_statuses")
    if expected_statuses is None:
        expected_statuses = [phase.get("expected_status", "")]
    expected_statuses = {str(status) for status in expected_statuses if status}
    target_aliases = [str(item) for item in phase.get("expected_target_aliases", [])]
    expected_alert = bool(phase.get("expected_alert", False))

    status_matches = []
    target_matches = []
    first_correct_latency = None
    observed_statuses = {}
    observed_targets = {}

    for sample in samples:
        status = str(sample.get("status", ""))
        target = sample.get("target_class")
        observed_statuses[status] = observed_statuses.get(status, 0) + 1
        if target:
            target = str(target)
            observed_targets[target] = observed_targets.get(target, 0) + 1
        status_ok = status in expected_statuses if expected_statuses else True
        target_ok = True
        if target_aliases:
            target_ok = bool(target) and label_matches(str(target), target_aliases)
        status_matches.append(status_ok)
        target_matches.append(target_ok)
        if status_ok and target_ok and first_correct_latency is None:
            first_correct_latency = sample.get("_received_monotonic", start_time) - start_time

    status_accuracy = (
        sum(1 for item in status_matches if item) / len(status_matches)
        if status_matches
        else 0.0
    )
    target_accuracy = (
        sum(1 for item in target_matches if item) / len(target_matches)
        if target_matches
        else 0.0
    )
    alert_observed = bool(alerts)
    alert_ok = alert_observed == expected_alert
    status_ok = status_accuracy >= float(phase.get("status_accuracy_threshold", 0.5))
    target_ok = (
        target_accuracy >= float(phase.get("target_accuracy_threshold", 0.5))
        if target_aliases
        else True
    )
    return {
        "type": "functionality",
        "module": "gaze",
        "case": phase_id,
        "success": status_ok and target_ok and alert_ok,
        "samples_total": len(samples),
        "status_accuracy": round(status_accuracy, 3),
        "target_accuracy": round(target_accuracy, 3) if target_aliases else None,
        "first_correct_latency_sec": round(first_correct_latency, 3)
        if first_correct_latency is not None
        else None,
        "expected_statuses": sorted(expected_statuses),
        "observed_statuses": observed_statuses,
        "expected_target_aliases": target_aliases,
        "observed_targets": observed_targets,
        "expected_alert": expected_alert,
        "alert_observed": alert_observed,
        "alert_count": len(alerts),
        "alerts": alerts,
    }


def run_gaze_attention_functionality(config, recorder):
    run_gaze_functionality(config, recorder)


def run_emotion_functionality(config, recorder):
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class LiveEmotionBenchmarkNode(Node):
        def __init__(self):
            super().__init__("therapy_emotion_benchmark")
            self.emotion_samples = []
            self.node_statuses = []
            self.create_subscription(String, "/child/emotion_status", self._emotion_cb, 10)
            self.create_subscription(String, "/gaze/node_status", self._node_status_cb, 10)

        def _emotion_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.emotion_samples.append(payload)

        def _node_status_cb(self, msg):
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                payload = {"raw": msg.data}
            payload["_received_monotonic"] = time.monotonic()
            self.node_statuses.append(payload)

        def wait_for_emotion_pipeline(self, timeout_sec):
            deadline = time.monotonic() + timeout_sec
            details = {}
            while time.monotonic() < deadline:
                details = {
                    "emotion_status_publishers": self.count_publishers("/child/emotion_status"),
                    "gaze_node_status_publishers": self.count_publishers("/gaze/node_status"),
                }
                ready_status = any(
                    item.get("state") == "READY" for item in self.node_statuses
                )
                details["gaze_ready_status_received"] = ready_status
                if all(value > 0 for value in details.values() if isinstance(value, int)) and ready_status:
                    details["ready"] = True
                    return details
                rclpy.spin_once(self, timeout_sec=0.1)
                time.sleep(0.1)
            details["ready"] = False
            return details

    rclpy.init(args=None)
    node = LiveEmotionBenchmarkNode()
    emotion_process = None
    try:
        emotion_cfg = nested_get(config, "functionality.emotion", {})
        startup_timeout = float(emotion_cfg.get("startup_timeout_sec", 120.0))
        start_launch = bool(emotion_cfg.get("start_launch", True))
        if start_launch:
            for _ in range(10):
                rclpy.spin_once(node, timeout_sec=0.1)
            if node.count_publishers("/child/emotion_status") == 0:
                target_classes = ",".join(str(item) for item in emotion_cfg.get("target_classes", []))
                command = [
                    "ros2",
                    "launch",
                    "gaze_estimation",
                    "gaze_with_attention.launch.py",
                    f"camera_index:={emotion_cfg.get('camera_index', 0)}",
                    f"device:={emotion_cfg.get('device', 'auto')}",
                    f"show_window:={str(bool(emotion_cfg.get('show_window', True))).lower()}",
                ]
                if target_classes:
                    command.append(f"target_classes:={target_classes}")
                emotion_process = subprocess.Popen(command, start_new_session=True)

        pipeline_details = node.wait_for_emotion_pipeline(startup_timeout)
        if not pipeline_details.get("ready"):
            recorder.record(
                {
                    "type": "functionality",
                    "module": "emotion",
                    "case": "emotion_pipeline_startup",
                    "success": False,
                    "details": pipeline_details,
                    "error": "emotion pipeline was not discovered or did not report READY",
                }
            )
            recorder.record(
                {
                    "type": "functionality_summary",
                    "module": "emotion",
                    "success": False,
                    "success_rate": 0.0,
                }
            )
            return

        recorder.record(
            {
                "type": "diagnostic",
                "module": "emotion",
                "case": "emotion_pipeline_startup",
                "success": True,
                "details": pipeline_details,
            }
        )

        phases = list(emotion_cfg.get("phases", []))
        default_duration = float(emotion_cfg.get("phase_duration_sec", 12.0))
        ignore_initial_sec = float(emotion_cfg.get("ignore_initial_sec", 1.0))
        prompt_before_each_phase = bool(emotion_cfg.get("prompt_before_each_phase", True))
        phase_results = []

        for index, phase in enumerate(phases, start=1):
            phase_id = str(phase.get("id", f"emotion_phase_{index}"))
            instruction = str(phase.get("instruction", phase_id))
            duration = float(phase.get("duration_sec", default_duration))
            print("")
            print(f"[emotion live benchmark] Phase {index}/{len(phases)}: {phase_id}")
            print(instruction)
            print(f"Duration: {duration:.1f}s")
            if prompt_before_each_phase and sys.stdin.isatty():
                input("Press ENTER when ready...")
            elif prompt_before_each_phase:
                print("Starting in 3 seconds...")
                time.sleep(3.0)

            phase_start = time.monotonic()
            phase_end = phase_start + duration
            while time.monotonic() < phase_end:
                rclpy.spin_once(node, timeout_sec=0.1)
                time.sleep(0.05)

            result = evaluate_emotion_phase(
                phase=phase,
                samples=[
                    sample for sample in node.emotion_samples
                    if phase_start + ignore_initial_sec
                    <= sample.get("_received_monotonic", 0.0)
                    <= phase_end
                ],
                start_time=phase_start,
                default_min_samples=int(emotion_cfg.get("min_samples", 2)),
                default_accuracy_threshold=float(
                    emotion_cfg.get("emotion_accuracy_threshold", 0.5)
                ),
            )
            phase_results.append(result)
            recorder.record(result)

        successes = sum(1 for item in phase_results if item.get("success"))
        recorder.record(
            {
                "type": "functionality_summary",
                "module": "emotion",
                "success": successes == len(phase_results) and bool(phase_results),
                "success_rate": round(successes / len(phase_results), 3)
                if phase_results
                else 0.0,
            }
        )
    finally:
        if emotion_process is not None:
            stop_process(emotion_process)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def evaluate_emotion_phase(
    *,
    phase,
    samples,
    start_time,
    default_min_samples,
    default_accuracy_threshold,
):
    phase_id = str(phase.get("id", "emotion_phase"))
    expected_aliases = [
        str(item) for item in phase.get("expected_emotion_aliases", [])
    ]
    min_samples = int(phase.get("min_samples", default_min_samples))
    accuracy_threshold = float(
        phase.get("emotion_accuracy_threshold", default_accuracy_threshold)
    )

    observed_emotions = {}
    valid_samples = []
    matches = []
    confidence_values = []
    first_correct_latency = None

    for sample in samples:
        emotion = str(sample.get("emotion", "")).strip()
        if not emotion:
            continue
        observed_emotions[emotion] = observed_emotions.get(emotion, 0) + 1
        if emotion in {"unavailable", "no_face"}:
            continue
        valid_samples.append(sample)
        ok = label_matches(emotion, expected_aliases)
        matches.append(ok)
        confidence = sample.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence_values.append(float(confidence))
        if ok and first_correct_latency is None:
            first_correct_latency = sample.get("_received_monotonic", start_time) - start_time

    accuracy = (
        sum(1 for item in matches if item) / len(matches)
        if matches
        else 0.0
    )
    dominant_emotion = None
    if observed_emotions:
        dominant_emotion = max(observed_emotions, key=observed_emotions.get)
    sample_count_ok = len(valid_samples) >= min_samples
    accuracy_ok = accuracy >= accuracy_threshold
    return {
        "type": "functionality",
        "module": "emotion",
        "case": phase_id,
        "success": sample_count_ok and accuracy_ok,
        "samples_total": len(samples),
        "valid_samples": len(valid_samples),
        "min_samples": min_samples,
        "expected_emotion_aliases": expected_aliases,
        "observed_emotions": observed_emotions,
        "dominant_emotion": dominant_emotion,
        "emotion_accuracy": round(accuracy, 3),
        "emotion_accuracy_threshold": accuracy_threshold,
        "mean_confidence": round(sum(confidence_values) / len(confidence_values), 3)
        if confidence_values
        else None,
        "first_correct_latency_sec": round(first_correct_latency, 3)
        if first_correct_latency is not None
        else None,
    }


class TaskDriver:
    def __init__(
        self,
        task_cfg,
        run_index,
        recorder,
        timeout_sec,
        child_response_timeout_sec,
        child_response_rating_threshold,
    ):
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile
        from std_msgs.msg import String

        class DriverNode(Node):
            def __init__(self, outer):
                super().__init__("therapy_task_benchmark_driver")
                self.outer = outer
                state_qos = QoSProfile(depth=1)
                state_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
                self.utterance_pub = self.create_publisher(String, "/hri/user_utterance", 10)
                self.benchmark_child_pub = self.create_publisher(
                    String, "/hri/benchmark_child_utterance", 10
                )
                self.dialog_command_pub = self.create_publisher(String, "/hri/dialog_command", 10)
                self.create_subscription(String, "/mission/state", self._state_cb, state_qos)
                self.create_subscription(String, "/mission/feedback", self._feedback_cb, 10)
                self.create_subscription(String, "/hri/dialog_command", self._dialog_command_cb, 10)
                self.create_subscription(String, "/hri/robot_utterance", self._robot_cb, 10)
                self.create_subscription(String, "/hri/dialog_state", self._dialog_state_cb, 10)
                self.create_subscription(String, "/hri/dialog_complete", self._dialog_complete_cb, 10)

            def _state_cb(self, msg):
                self.outer.on_state(msg.data.strip())

            def _feedback_cb(self, msg):
                self.outer.feedback.append((time.monotonic(), msg.data))

            def _dialog_command_cb(self, msg):
                self.outer.on_dialog_command(msg.data)

            def _robot_cb(self, msg):
                self.outer.on_robot_utterance(msg.data)

            def _dialog_state_cb(self, msg):
                self.outer.on_dialog_state(msg.data)

            def _dialog_complete_cb(self, msg):
                self.outer.on_dialog_complete(msg.data)

            def publish_utterance(self, text):
                msg = String()
                msg.data = text
                self.utterance_pub.publish(msg)

            def publish_benchmark_child_utterance(self, text):
                msg = String()
                msg.data = text
                self.benchmark_child_pub.publish(msg)

            def publish_dialog_command(self, payload):
                msg = String()
                msg.data = json.dumps(payload)
                self.dialog_command_pub.publish(msg)

        self.rclpy = rclpy
        self.task_cfg = task_cfg
        self.run_index = run_index
        self.recorder = recorder
        self.timeout_sec = timeout_sec
        self.child_response_timeout_sec = child_response_timeout_sec
        self.child_response_rating_threshold = child_response_rating_threshold
        self.node = DriverNode(self)
        self.start_time = time.monotonic()
        self.state_times = {}
        self.feedback = []
        self.sent_states = set()
        self.completed = False
        self.failed = False
        self.robot_utterances = []
        self.dialog_states = []
        self.child_activity_started_at = None
        self.child_activity_context = {}
        self.child_activity_timed_out = False
        self.child_opening_text = None
        self.benchmark_child_utterance = str(
            task_cfg.get(
                "benchmark_child_utterance",
                "Yes, I am ready. Can we continue?",
            )
        )
        self.child_utterance_sent_at = None
        self.child_llm_response = None
        self.child_llm_response_at = None
        self.child_llm_response_ready_at = None
        self.child_response_rating = None
        self.child_response_rating_requested = False
        self.child_response_success = False
        self.child_stop_sent = False
        self.dialog_complete_seen = False
        self.dialog_complete_payload = {}
        child_suffix = f"{task_cfg['id']}_{int(time.time())}_{run_index}"
        self.child_name = f"Benchmark {child_suffix}"

    def on_state(self, state):
        self.state_times.setdefault(state, time.monotonic() - self.start_time)
        if state in self.sent_states:
            return
        if state == "WAIT_CHILD_NAME":
            self.node.publish_utterance(self.child_name)
            self.sent_states.add(state)
        elif state == "WAIT_TASK":
            self.node.publish_utterance(self.task_cfg["task_description"])
            self.sent_states.add(state)
        elif state == "WAIT_ACTIVITY":
            self.node.publish_utterance(self.task_cfg["activity_description"])
            self.sent_states.add(state)
        elif state == "WAIT_THERAPIST_OVERRIDE":
            self.node.publish_utterance(self.task_cfg["activity_description"])
            self.sent_states.add(state)
        elif state == "MISSION_COMPLETE":
            self.completed = True
        elif state == "MISSION_FAILED":
            self.failed = True

    def on_dialog_command(self, raw):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if payload.get("command") == "start_child_activity":
            if self.child_activity_started_at is None:
                self.child_activity_started_at = time.monotonic()
                self.child_activity_context = dict(payload.get("context", {}))
                print("")
                print(f"[Task benchmark] Child activity started for {self.task_cfg['id']}.")

    def on_robot_utterance(self, text):
        self.robot_utterances.append(
            {"text": text, "_received_monotonic": time.monotonic()}
        )

    def on_dialog_state(self, state):
        self.dialog_states.append(
            {"state": state, "_received_monotonic": time.monotonic()}
        )

    def on_dialog_complete(self, raw):
        self.dialog_complete_seen = True
        try:
            self.dialog_complete_payload = json.loads(raw)
        except json.JSONDecodeError:
            self.dialog_complete_payload = {"raw": raw}

    def maybe_drive_child_interaction(self):
        if self.child_activity_started_at is None:
            return
        if self.child_utterance_sent_at is None:
            opening = first_after(self.robot_utterances, self.child_activity_started_at)
            if opening is None:
                return
            self.child_opening_text = str(opening.get("text", ""))
            self.node.publish_benchmark_child_utterance(self.benchmark_child_utterance)
            self.child_utterance_sent_at = time.monotonic()
            print("")
            print("[Task benchmark] Robot opening:")
            print(self.child_opening_text or "(empty opening)")
            print("[Task benchmark] Injected child response:")
            print(self.benchmark_child_utterance)
            return

        if self.child_llm_response is None:
            response = first_after(self.robot_utterances, self.child_utterance_sent_at)
            if response is not None:
                self.child_llm_response = str(response.get("text", ""))
                self.child_llm_response_at = float(response.get("_received_monotonic", time.monotonic()))
                return
            if (
                time.monotonic() - self.child_utterance_sent_at
                > self.child_response_timeout_sec
            ):
                self.child_activity_timed_out = True
                self.publish_stop_once()
            return

        if self.child_response_rating_requested:
            return
        response_ready = first_state_after(
            self.dialog_states,
            self.child_llm_response_at or time.monotonic(),
            "ready",
        )
        if response_ready is None:
            if (
                self.child_llm_response_at is not None
                and time.monotonic() - self.child_llm_response_at
                > self.child_response_timeout_sec
            ):
                self.child_activity_timed_out = True
            else:
                return

        if response_ready is not None:
            self.child_llm_response_ready_at = float(
                response_ready.get("_received_monotonic", time.monotonic())
            )
        self.child_response_rating_requested = True
        self.child_response_rating = ask_response_rating(
            case_id=f"{self.task_cfg['id']}_child_response",
            robot_text=self.child_llm_response,
            benchmark_label="Task benchmark",
        )
        self.child_response_success = (
            self.child_response_rating is not None
            and self.child_response_rating > self.child_response_rating_threshold
        )
        self.publish_stop_once()

    def publish_stop_once(self):
        if self.child_stop_sent:
            return
        self.child_stop_sent = True
        self.node.publish_dialog_command({"command": "stop"})

    def run_until_done(self):
        deadline = time.monotonic() + self.timeout_sec
        while time.monotonic() < deadline and not self.completed and not self.failed:
            self.rclpy.spin_once(self.node, timeout_sec=0.1)
            self.maybe_drive_child_interaction()
        duration = time.monotonic() - self.start_time
        final_state = "MISSION_COMPLETE" if self.completed else "MISSION_FAILED" if self.failed else "TIMEOUT"
        self.node.destroy_node()
        return {
            "completed": self.completed,
            "final_state": final_state,
            "duration_sec": round(duration, 3),
            "state_times": {key: round(value, 3) for key, value in self.state_times.items()},
            "feedback": [text for _, text in self.feedback[-40:]],
            "child_interaction_observed": self.child_activity_started_at is not None,
            "child_opening_text": self.child_opening_text,
            "benchmark_child_utterance": self.benchmark_child_utterance,
            "child_llm_response": self.child_llm_response,
            "child_llm_response_ready": self.child_llm_response_ready_at is not None,
            "response_rating": round(self.child_response_rating, 1)
            if self.child_response_rating is not None
            else None,
            "response_rating_threshold": self.child_response_rating_threshold,
            "child_response_success": self.child_response_success,
            "child_activity_timed_out": self.child_activity_timed_out,
            "dialog_complete_seen": self.dialog_complete_seen,
            "dialog_complete_payload": self.dialog_complete_payload,
        }


def run_task_benchmarks(args, config, recorder):
    import rclpy

    tasks = nested_get(config, "tasks", [])
    requested = set(args.tasks.split(",")) if args.tasks else {task["id"] for task in tasks}
    selected = [task for task in tasks if task["id"] in requested or "all" in requested]
    if not selected:
        raise SystemExit(f"No benchmark tasks selected from: {sorted(requested)}")

    rclpy.init(args=None)
    try:
        for task_cfg in selected:
            for run_index in range(1, args.runs + 1):
                clean_empower_output()
                process = start_mission_process(args, config)
                driver = TaskDriver(
                    task_cfg=task_cfg,
                    run_index=run_index,
                    recorder=recorder,
                    timeout_sec=float(nested_get(config, "benchmark.mission_timeout_sec", 900.0)),
                    child_response_timeout_sec=float(
                        nested_get(config, "benchmark.child_response_timeout_sec", 180.0)
                    ),
                    child_response_rating_threshold=float(
                        nested_get(config, "benchmark.child_response_rating_threshold", 6.0)
                    ),
                )
                try:
                    result = driver.run_until_done()
                    artifact_snapshot_dir = (
                        recorder.paths.run_dir
                        / "artifacts"
                        / f"{task_cfg['id']}_run_{run_index}"
                    )
                    result.update(
                        evaluate_task_artifacts(
                            task_cfg,
                            artifact_snapshot_dir=artifact_snapshot_dir,
                        )
                    )
                    result.update(
                        {
                            "type": "task",
                            "task_id": task_cfg["id"],
                            "run_index": run_index,
                            "success": bool(
                                result["completed"]
                                and result.get("plan_ok", False)
                                and result.get("perception_ok", False)
                                and result.get("child_response_success", False)
                            ),
                        }
                    )
                    recorder.record(result)
                finally:
                    stop_process(process)
                    time.sleep(2.0)
    finally:
        if rclpy.ok():
            rclpy.shutdown()


def clean_empower_output():
    output_dir = Path(DEFAULT_EMPOWER_OUTPUT)
    if output_dir.exists():
        shutil.rmtree(output_dir)


def start_mission_process(args, config):
    command = [
        "ros2",
        "launch",
        "empower",
        "hri_empower_mission.launch.py",
        "start_hri:=true",
        "start_dialog_bubbles:=false",
        f"start_models_cacher:={str(args.start_models_cacher).lower()}",
        "hri_input_mode:=text",
        "therapist_input_enabled:=false",
        "benchmark_child_utterances_enabled:=true",
        f"benchmark_child_utterance_timeout_sec:={float(nested_get(config, 'benchmark.child_response_timeout_sec', 180.0))}",
    ]
    if args.mission_params_file:
        command.append(f"mission_params_file:={args.mission_params_file}")
    if args.empower_params_file:
        command.append(f"empower_params_file:={args.empower_params_file}")
    return subprocess.Popen(command, start_new_session=True)


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)
        except ProcessLookupError:
            pass


def stop_hri_process(process):
    if process.poll() is not None:
        return
    if process.stdin is not None:
        try:
            process.stdin.write("/quit\n")
            process.stdin.flush()
            process.wait(timeout=10.0)
            return
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
    stop_process(process)


def evaluate_task_artifacts(
    task_cfg,
    artifact_snapshot_dir = None,
):
    dump_dir = Path(DEFAULT_EMPOWER_OUTPUT)
    plan_text = read_text(dump_dir / "symbolic_plan.txt")
    planning_json = read_json(dump_dir / "planning.json", {})
    detections = read_json(dump_dir / "detections.json", {})
    global_detections = read_json(dump_dir / "global_detections.json", [])
    global_scene_graph = read_json(dump_dir / "global_scene_graph.json", {})
    expected_keywords = [normalize_label(k) for k in task_cfg.get("expected_plan_keywords", [])]
    normalized_plan = normalize_label(plan_text)
    plan_ok = all(keyword in normalized_plan for keyword in expected_keywords)
    labels = artifact_labels(detections, global_detections, global_scene_graph)
    expected_object = task_cfg.get("expected_object", "")
    perception_ok = any(
        label_matches(label, [expected_object, *object_aliases(expected_object)])
        for label in labels
    )
    artifact_files = sorted(path.name for path in dump_dir.iterdir()) if dump_dir.exists() else []
    snapshot_path = ""
    if artifact_snapshot_dir is not None and dump_dir.exists():
        if artifact_snapshot_dir.exists():
            shutil.rmtree(artifact_snapshot_dir)
        shutil.copytree(dump_dir, artifact_snapshot_dir)
        snapshot_path = str(artifact_snapshot_dir)
    return {
        "plan_ok": plan_ok,
        "perception_ok": perception_ok,
        "observed_detection_labels": labels,
        "symbolic_plan": plan_text.strip(),
        "planning_json_available": bool(planning_json),
        "artifact_dir": str(dump_dir),
        "artifact_files": artifact_files,
        "artifact_snapshot_dir": snapshot_path,
    }


def artifact_labels(*sources):
    labels = []

    def visit(value):
        if isinstance(value, dict):
            label = value.get("label")
            if label:
                labels.append(str(label))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for source in sources:
        visit(source)
    return sorted(set(labels))


def object_aliases(label):
    aliases = {
        "book": ["red book", "clear red book"],
        "teddy bear": ["teddy", "peluche", "plush toy", "stuffed animal"],
        "computer": ["laptop", "laptop computer"],
    }
    return aliases.get(label, [])


def run_functionality(args, config, recorder):
    raw_modules = set(args.modules.split(",")) if args.modules else {"nav2", "perception", "hri", "gaze", "emotion"}
    module_aliases = {
        "hri": "hri",
        "hri_e2e": "hri",
        "gaze": "gaze",
        "gaze_live": "gaze",
        "gaze_attention": "gaze",
        "emotion": "emotion",
        "emotion_live": "emotion",
    }
    modules = {
        module_aliases.get(module.strip(), module.strip())
        for module in raw_modules
        if module.strip()
    }
    supported = {
        "nav2",
        "perception",
        "hri",
        "gaze",
        "emotion",
    }
    for module in sorted(modules - supported):
        recorder.record(
            {
                "type": "functionality",
                "module": module,
                "case": "unsupported_module",
                "success": False,
                "error": "This functionality benchmark is not implemented.",
            }
        )
    if "perception" in modules:
        run_perception_functionality(config, recorder)
    if "hri" in modules:
        run_hri_functionality(config, recorder)
    if "nav2" in modules:
        run_nav2_functionality(config, recorder)
    if "gaze" in modules:
        run_gaze_functionality(config, recorder)
    if "emotion" in modules:
        run_emotion_functionality(config, recorder)


def summarize_records(records):
    groups = {}
    for record in records:
        key = record.get("module") or record.get("task_id") or record.get("type", "unknown")
        groups.setdefault(str(key), []).append(record)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "record_count": len(records),
        "groups": {},
    }
    for key, items in groups.items():
        metric_items = [
            item for item in items
            if not str(item.get("type", "")).endswith("_summary")
            and item.get("type") != "diagnostic"
        ]
        success_values = [
            bool(item.get("success")) for item in metric_items if "success" in item
        ]
        durations = [
            float(item.get("duration_sec", item.get("turn_latency_sec")))
            for item in metric_items
            if isinstance(item.get("duration_sec", item.get("turn_latency_sec")), (int, float))
        ]
        latencies = [
            float(item["latency_ms"])
            for item in metric_items
            if isinstance(item.get("latency_ms"), (int, float))
        ]
        ratings = [
            float(item["response_rating"])
            for item in metric_items
            if isinstance(item.get("response_rating"), (int, float))
        ]
        emotion_accuracies = [
            float(item["emotion_accuracy"])
            for item in metric_items
            if isinstance(item.get("emotion_accuracy"), (int, float))
        ]
        summary["groups"][key] = {
            "count": len(items),
            "measured_count": len(metric_items),
            "success_rate": round(sum(success_values) / len(success_values), 3)
            if success_values
            else None,
            "mean_duration_sec": round(sum(durations) / len(durations), 3)
            if durations
            else None,
            "mean_latency_ms": round(sum(latencies) / len(latencies), 3)
            if latencies
            else None,
            "mean_response_rating": round(sum(ratings) / len(ratings), 2)
            if ratings
            else None,
            "mean_emotion_accuracy": round(
                sum(emotion_accuracies) / len(emotion_accuracies), 3
            )
            if emotion_accuracies
            else None,
        }
    return summary


def render_markdown(summary):
    lines = [
        "# Therapy Robot Benchmark Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        "| Benchmark | Records | Success rate | Mean duration (s) | Mean latency (ms) | Mean response rating | Mean emotion accuracy |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, item in summary["groups"].items():
        success = item["success_rate"]
        duration = item["mean_duration_sec"]
        latency = item["mean_latency_ms"]
        rating = item.get("mean_response_rating")
        emotion_accuracy = item.get("mean_emotion_accuracy")
        count = item.get("measured_count", item["count"])
        lines.append(
            f"| {key} | {count} | "
            f"{success if success is not None else 'N/A'} | "
            f"{duration if duration is not None else 'N/A'} | "
            f"{latency if latency is not None else 'N/A'} | "
            f"{rating if rating is not None else 'N/A'} | "
            f"{emotion_accuracy if emotion_accuracy is not None else 'N/A'} |"
        )
    lines.append("")
    return "\n".join(lines)


def aggregate_existing(run_dir):
    path = Path(run_dir)
    records = []
    for results_path in sorted(path.glob("**/results.jsonl")):
        with results_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    records.append(json.loads(line))
    summary = summarize_records(records)
    (path / "aggregate_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (path / "aggregate_summary.md").write_text(render_markdown(summary), encoding="utf-8")
    print(path / "aggregate_summary.md")


def build_parser():
    parser = argparse.ArgumentParser(description="Run TIAGo therapy-room benchmarks.")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    func = sub.add_parser("functionality", help="Run functionality benchmarks.")
    func.add_argument("--modules", default="nav2,perception,hri,gaze,emotion")
    func.add_argument("--run-id", default="")
    func.add_argument("--output-root", default="")

    task = sub.add_parser("task", help="Run task benchmarks by spawning the mission launch.")
    task.add_argument("--tasks", default="all")
    task.add_argument("--runs", type=int, default=1)
    task.add_argument("--run-id", default="")
    task.add_argument("--output-root", default="")
    task.add_argument("--mission-params-file", default="")
    task.add_argument("--empower-params-file", default="")
    task.add_argument(
        "--start-models-cacher",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start EMPOWER models_cacher_node inside the spawned mission launch.",
    )

    report = sub.add_parser("report", help="Aggregate existing benchmark JSONL files.")
    report.add_argument("--run-dir", default="/root/exchange/output/benchmark_output")
    return parser


def main(argv = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "report":
        aggregate_existing(args.run_dir)
        return 0

    config = load_config(args.config)
    output_root = args.output_root or nested_get(config, "benchmark.output_root", "/root/exchange/output/benchmark_output")
    run_id = args.run_id or now_id(args.command)
    recorder = BenchmarkRecorder(output_root, run_id)
    if args.command == "functionality":
        run_functionality(args, config, recorder)
    elif args.command == "task":
        run_task_benchmarks(args, config, recorder)
    summary = recorder.write_summary()
    print(recorder.paths.run_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

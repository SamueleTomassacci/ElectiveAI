import json
import math
import os
import signal
import subprocess
import threading
import time
from enum import Enum

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy,QoSProfile
from std_msgs.msg import String

from empower.static_scene_graph import StaticSceneGraph,StaticSceneGraphError, default_scene_graph_path

try:
    from llm_interaction.session_scores import (
        ACTIVITY_TO_TASK,
        infer_activity_and_task,
    )
except ImportError:
    ACTIVITY_TO_TASK = {
        "storytelling": "Take the book and give it to the child",
        "mood_check_teddy": "Take the teddy bear and give it to the child",
        "computer_emotion_recognition": "Take the computer and give it to the child",
    }

    def infer_activity_and_task(text):
        return None

try:
    from llm_interaction.rdf_child_memory import RdfChildMemoryStore
except ImportError:
    RdfChildMemoryStore = None


class MissionState(str, Enum):
    GO_TO_THERAPIST = "GO_TO_THERAPIST"
    WAIT_CHILD_NAME = "WAIT_CHILD_NAME"
    ACK_CHILD_NAME = "ACK_CHILD_NAME"
    PROPOSE_ACTIVITY = "PROPOSE_ACTIVITY"
    WAIT_THERAPIST_OVERRIDE = "WAIT_THERAPIST_OVERRIDE"
    ACK_PROPOSAL = "ACK_PROPOSAL"
    WAIT_TASK = "WAIT_TASK"

    ACK_TASK = "ACK_TASK"
    WAIT_ACTIVITY = "WAIT_ACTIVITY"
    ACK_ACTIVITY = "ACK_ACTIVITY"
    GO_TO_SPAWN = "GO_TO_SPAWN"
    RUN_EMPOWER = "RUN_EMPOWER"
    GO_TO_CHILD = "GO_TO_CHILD"
    CHILD_READY = "CHILD_READY"
    CHILD_ACTIVITY = "CHILD_ACTIVITY"
    GO_BACK_TO_THERAPIST = "GO_BACK_TO_THERAPIST"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    MISSION_FAILED = "MISSION_FAILED"


class MissionInteractionSpec:
    def __init__(
        self,
        task_description="",
        activity_description="",
        activity_type="generic_child_activity",
        child_name="",
    ):
        self.task_description = task_description
        self.activity_description = activity_description

        self.activity_type = activity_type
        self.child_name = child_name


class MissionOrchestratorNode(Node):

    def __init__(self):
        super().__init__("mission_orchestrator_node")
        self.declare_node_parameters()

        status_qos = QoSProfile(depth=1)
        status_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.state_pub = self.create_publisher(String,"/mission/state",status_qos)
        self.feedback_pub = self.create_publisher(String,"/mission/feedback", 10)
        self.task_pub = self.create_publisher(String, "/mission/task_description", status_qos)
        self.child_ready_pub = self.create_publisher(String, "/mission/child_ready", status_qos)
        self.dialog_command_pub = self.create_publisher(String, "/hri/dialog_command",10)

        self.create_subscription(String,"/hri/user_utterance", self.task_callback,10)
        self.create_subscription(String, "/hri/dialog_complete", self.dialog_complete_callback,10)
        self.create_subscription(String,"/mission/gaze_command", self.gaze_command_callback, 10)
        self.create_subscription(String,"/gaze/node_status", self.gaze_node_status_callback, 10)

        self.skip_mode = bool(self.get_parameter("skip_to_child_activity").value)
        if not self.skip_mode:
            self.nav_client = ActionClient(
                self,
                NavigateToPose,
                str(self.get_parameter("nav_action_name").value),
            )
        else:
            self.nav_client = None
        self.state_lock = threading.Lock()
        self.state = None
        self.interaction_spec = MissionInteractionSpec(
            activity_type=str(self.get_parameter("default_activity_type").value)
        )
        self.task_event = threading.Event()
        self.activity_event = threading.Event()
        self.child_name_event = threading.Event()
        self.override_event = threading.Event()
        self.override_text = ""
        self.child_name = ""
        self.stop_event = threading.Event()
        self.dialog_complete_event = threading.Event()

        self.dialog_complete_payload = ""
        self.mission_thread = None
        self.gaze_process = None
        self.gaze_process_label = "gaze estimation"
        self.gaze_ready_event = threading.Event()
        self.gaze_error_message = ""
        self.child_memory_store = None
        self.static_scene_graph = self.load_static_scene_graph()

        if bool(self.get_parameter("auto_start").value):
            self.mission_thread = threading.Thread(
                target=self.run_mission,
                name="hri_empower_mission",
                daemon=True,
            )
            self.mission_thread.start()
        else:
            self.publish_feedback("Mission orchestrator ready; auto_start is disabled")

    def declare_node_parameters(self):
        self.declare_parameter("map_frame","map")
        self.declare_parameter("static_scene_graph_path",default_scene_graph_path())
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter(
            "params_file",
            "/root/exchange/src/empower/config/empower_config.yaml",
        )

        self.declare_parameter("wait_for_task_timeout_sec", 120.0)
        self.declare_parameter("wait_for_activity_timeout_sec",120.0)
        self.declare_parameter("wait_for_child_name_timeout_sec",120.0)
        self.declare_parameter("task_ack_text","Okay, I understood the task.")
        self.declare_parameter("task_ask_text", "What task should I do?")

        self.declare_parameter(
            "activity_ask_text",
            "What activity should I do with the child?",
        )
        self.declare_parameter(
            "activity_ack_text",
            "Okay, I understood the activity.",
        )
        self.declare_parameter(
            "child_name_ack_text",
            "I know {child_name}.",
        )

        self.declare_parameter(
            "unknown_child_name_text",
            "I don't know {child_name}.",
        )
        self.declare_parameter("default_activity_type", "generic_child_activity")
        self.declare_parameter("auto_start",True)
        self.declare_parameter("action_server_timeout_sec",20.0)
        self.declare_parameter("nav_result_timeout_sec",300.0)
        self.declare_parameter("empower_process_timeout_sec", 0.0)
        self.declare_parameter("capture_timeout_sec",60.0)
        self.declare_parameter("capture_before_empower", True)
        self.declare_parameter("execute_empower_plan", True)

        self.declare_parameter("child_delivery_handled_by_empower",False)
        self.declare_parameter("use_case","therapy_task")
        self.declare_parameter("output_root","/root/exchange/output")

        self.declare_parameter("storytelling_timeout_sec", 300.0)
        self.declare_parameter("child_activity_timeout_sec", 300.0)
        self.declare_parameter("gaze_estimation.enabled", True)
        self.declare_parameter("gaze_estimation.camera_index",0)
        self.declare_parameter("gaze_estimation.device", "auto")
        self.declare_parameter("gaze_estimation.show_window",True)
        self.declare_parameter("gaze_estimation.startup_timeout_sec", 30.0)
        self.declare_parameter("proposal_acceptance_timeout_sec", 15.0)
        self.declare_parameter(
            "session_scores_path",
            "/root/exchange/output/interaction_output/session_scores.json",
        )
        self.declare_parameter(
            "child_memory_rdf_path",
            "/root/exchange/output/interaction_output/children_memory.ttl",
        )
        self.declare_parameter("child_memory_seed_if_missing", True)
        self.declare_parameter(
            "child_name_ask_text",
            "Hello! Which child do we have today?",
        )
        self.declare_parameter(
            "no_history_text",
            "I don't have a preferred activity in memory for {child_name} yet.",
        )
        self.declare_parameter(
            "proposal_text",
            "I remember that {child_name} prefers {activity_label}. Is that okay for today?",
        )
        self.declare_parameter(
            "proposal_rejected_text",
            "Okay.",
        )
        self.declare_parameter("skip_to_child_activity", False)

        self.declare_parameter("test_child_name", "Luca")
        self.declare_parameter("test_activity_type", "storytelling")
        self.declare_parameter("test_task_description", "Take the book and give it to the child")

    def task_callback(self, msg):
        text = msg.data.strip()
        if not text:
            return
        state = self.current_state()
        INPUT_STATES = {
            MissionState.WAIT_CHILD_NAME,
            MissionState.WAIT_TASK,
            MissionState.WAIT_ACTIVITY,
            MissionState.WAIT_THERAPIST_OVERRIDE,
        }
        if state not in INPUT_STATES:
            self.publish_feedback(
                f"Ignoring HRI utterance outside therapist input capture: {text}",
                log_level="debug",
            )
            return
        if state == MissionState.WAIT_CHILD_NAME:
            self.capture_child_name(text)
            return
        if state == MissionState.WAIT_TASK:
            self.capture_physical_task(text)
            return
        if state == MissionState.WAIT_ACTIVITY:
            self.capture_child_activity(text)
            return
        if state == MissionState.WAIT_THERAPIST_OVERRIDE:
            self.override_text = text
            self.override_event.set()

    def capture_physical_task(self, text):
        if self.interaction_spec.task_description:

            self.publish_feedback(
                f"Ignoring extra HRI utterance after task capture: {text}",
                log_level="debug",
            )
            return
        self.interaction_spec.task_description = text
        self.publish(self.task_pub, text)
        self.publish_feedback(f"Received physical task: {text}")
        self.task_event.set()

    def capture_child_activity(self,text):

        if self.interaction_spec.activity_description:
            self.publish_feedback(
                f"Ignoring extra HRI utterance after activity capture: {text}",
                log_level="debug",
            )
            return
        self.interaction_spec.activity_description = text
        self.interaction_spec.activity_type = self.normalize_activity_type(text)
        self.publish_feedback(f"Received child activity: {text}")
        self.publish_feedback(
            f"Normalized activity type: {self.interaction_spec.activity_type}"
        )
        self.activity_event.set()

    def capture_child_name(self, text):
        if self.interaction_spec.child_name:

            self.publish_feedback(
                f"Ignoring extra HRI utterance after child name capture: {text}",
                log_level="debug",
            )
            return
        child_name = text.strip().strip(".!?;:,")
        self.interaction_spec.child_name = child_name
        self.child_name = child_name
        self.publish_feedback(f"Received child name: {child_name}")
        self.child_name_event.set()

    def dialog_complete_callback(self,msg):
        self.dialog_complete_payload = msg.data
        self.dialog_complete_event.set()

    def run_mission(self):
        try:
            if bool(self.get_parameter("skip_to_child_activity").value):
                self.run_mission_skip_to_child()
            else:
                self.run_mission_full()
        except Exception as exc:
            self.stop_gaze_process()
            self.publish_feedback(
                f"Mission failed with {type(exc).__name__}: {exc}",
                log_level="error",
            )

    def run_mission_skip_to_child(self):
        child_name = str(self.get_parameter("test_child_name").value)
        activity_type= str(self.get_parameter("test_activity_type").value)
        task_desc = str(self.get_parameter("test_task_description").value)

        # Test mode skips therapist dialogue
        self.interaction_spec.child_name = child_name
        self.interaction_spec.activity_type = activity_type
        self.interaction_spec.task_description = task_desc
        self.interaction_spec.activity_description = self.activity_label(activity_type)
        self.child_name = child_name

        self.publish_feedback(
            f"[TEST MODE] Skipping to child activity: "
            f"child={child_name}, activity={activity_type}, task={task_desc}"
        )

        self.simulate_drop_on_child_table(task_desc)

        self.transition(MissionState.CHILD_READY)
        self.publish(self.child_ready_pub, "child_ready")

        if not self.start_gaze_process():
            self.publish_feedback(
                "Gaze estimation failed to start; continuing without gaze",
                log_level="warn",
            )

        self.transition(MissionState.CHILD_ACTIVITY)
        self.run_child_activity()

        self.stop_gaze_process()

        self.transition(MissionState.MISSION_COMPLETE)

        self.publish_feedback("Mission complete")

    def simulate_drop_on_child_table(self, task_desc):
        params_file = str(self.get_parameter("params_file").value)
        drop_pose =self.static_scene_graph.resolve_drop_pose(role="child_delivery_drop")
        if drop_pose is None:
            self.publish_feedback("No child delivery drop pose in scene graph",log_level="warn")
            return
        task_lower = task_desc.lower()
        object_keys = {
            "book": "clear_red_book_on_right_table",
            "teddy": "teddy_bear_on_right_table",
            "peluche": "teddy_bear_on_right_table",
            "computer": "laptop_computer_on_right_table",
            "laptop": "laptop_computer_on_right_table",
        }
        entity = None

        drop_z = 0.01
        for keyword,gazebo_entity in object_keys.items():
            if keyword in task_lower:

                entity = gazebo_entity
                break
        if entity is None:
            self.publish_feedback(f"No matching Gazebo object for task: {task_desc}",log_level="warn")
            return

        gz_cmd = self.empower_param(params_file,"object_simulation.gz_command", "gz")
        gz_world = self.empower_param(params_file,"object_simulation.gz_world_name", "scene")
        x,y,z = drop_pose["x"],drop_pose["y"],drop_pose["z"]
        cmd = [
            gz_cmd,"model", "-w",gz_world, "-m", entity,
            "--pose",f"{x},{y},{z+ drop_z},0,0,0",
        ]
        self.publish_feedback(f"[TEST MODE] Dropping {entity} at child table ({x}, {y}, {z})")
        try:
            subprocess.run(cmd, timeout=10.0,check=False)
        except Exception as exc:
            self.publish_feedback(f"Gazebo drop failed: {exc}", log_level="warn")

    def run_mission_full(self):

        self.transition(MissionState.GO_TO_THERAPIST)
        if not self.navigate_to_pose(
            "therapist",
            self.static_nav_pose("therapist","receive_instruction"),
        ):

            return

        self.transition(MissionState.WAIT_CHILD_NAME)
        self.say(str(self.get_parameter("child_name_ask_text").value))

        if not self.wait_for_child_name():
            return

        self.transition(MissionState.ACK_CHILD_NAME)
        child_memory = self.child_memory_for_activity_proposal(
            self.interaction_spec.child_name
        )
        if child_memory is not None:
            self.say(self.child_name_ack_text())
        else:

            self.say(self.unknown_child_name_text())

        self.transition(MissionState.PROPOSE_ACTIVITY)
        # Memory before manual input
        proposal = self.propose_activity(child_memory)

        if proposal is not None:
            self.transition(MissionState.WAIT_THERAPIST_OVERRIDE)
            response = self.wait_for_proposal_response()
            if response == "yes":
                self.publish_feedback("Therapist accepted the memory-based proposal")
                self.transition(MissionState.ACK_PROPOSAL)
                self.say(
                    f"Okay! I will do {self.activity_label(self.interaction_spec.activity_type)} "
                    f"with {self.interaction_spec.child_name}."
                )
            else:
                if response == "no":
                    self.publish_feedback("Therapist rejected the memory-based proposal")
                else:
                    self.publish_feedback(
                        "No clear yes/no response for the proposal; asking for manual task and activity",
                        log_level="warn",
                    )
                self.reset_task_and_activity()

                self.say(str(self.get_parameter("proposal_rejected_text").value))
                if not self.collect_manual_task_and_activity():
                    return
        else:
            self.say(
                str(self.get_parameter("no_history_text").value).format(
                    child_name=self.interaction_spec.child_name
                )
            )
            if not self.collect_manual_task_and_activity():
                return

        self.transition(MissionState.GO_TO_SPAWN)
        if not self.navigate_to_pose(
            "spawn",
            self.static_nav_pose("center","capture_origin"),
        ):
            return

        self.transition(MissionState.RUN_EMPOWER)
        if not self.run_empower(self.interaction_spec.task_description):
            return

        # Delivery already in plan
        if bool(self.get_parameter("child_delivery_handled_by_empower").value):
            self.publish_feedback(
                "Skipping hardcoded GO_TO_CHILD because EMPOWER handles child delivery"
            )
        else:

            self.transition(MissionState.GO_TO_CHILD)
            if not self.navigate_to_pose(
                "child",
                self.static_nav_pose("child", "deliver_object"),
            ):
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

        self.transition(MissionState.GO_BACK_TO_THERAPIST)
        self.navigate_to_pose(
            "therapist",
            self.static_nav_pose("therapist","receive_instruction"),
        )
        self.say(self.build_activity_summary())

        self.transition(MissionState.MISSION_COMPLETE)
        self.publish_feedback("Mission complete")

    def wait_for_task(self):
        timeout_sec = float(self.get_parameter("wait_for_task_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the task on /hri/user_utterance"
        )
        if self.task_event.wait(timeout=timeout_sec):
            return True
        self.publish_feedback(
            "Timed out waiting for a task from /hri/user_utterance",
            log_level="error",
        )
        return False

    def wait_for_child_name(self):
        timeout_sec = float(self.get_parameter("wait_for_child_name_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the child name on /hri/user_utterance"
        )
        if self.child_name_event.wait(timeout=timeout_sec):
            return True

        self.publish_feedback(
            "Timed out waiting for the child name from /hri/user_utterance",
            log_level="error",
        )
        return False

    def child_name_ack_text(self):
        template = str(self.get_parameter("child_name_ack_text").value)
        return template.format(child_name=self.interaction_spec.child_name)

    def unknown_child_name_text(self):
        template = str(self.get_parameter("unknown_child_name_text").value)
        return template.format(child_name=self.interaction_spec.child_name)

    def wait_for_activity(self):

        timeout_sec = float(self.get_parameter("wait_for_activity_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for the child activity on /hri/user_utterance"
        )
        if self.activity_event.wait(timeout=timeout_sec):
            return True
        self.publish_feedback(
            "Timed out waiting for a child activity from /hri/user_utterance",
            log_level="error",
        )
        return False

    def collect_manual_task_and_activity(self):

        self.transition(MissionState.WAIT_TASK)
        self.say(str(self.get_parameter("task_ask_text").value))

        if not self.wait_for_task():
            return False
        self.transition(MissionState.ACK_TASK)
        self.say(str(self.get_parameter("task_ack_text").value))

        self.transition(MissionState.WAIT_ACTIVITY)
        self.say(str(self.get_parameter("activity_ask_text").value))
        if not self.wait_for_activity():
            return False

        self.transition(MissionState.ACK_ACTIVITY)
        self.say(str(self.get_parameter("activity_ack_text").value))
        return True

    def reset_task_and_activity(self):
        self.interaction_spec.task_description = ""
        self.interaction_spec.activity_description = ""
        self.interaction_spec.activity_type = str(
            self.get_parameter("default_activity_type").value
        )
        self.task_event.clear()
        self.activity_event.clear()

    def say(self, text):

        payload =json.dumps({"command": "say","text": text})
        self.publish(self.dialog_command_pub, payload)
        self.publish_feedback(f"Requested robot speech: {text}")

    def run_child_activity(self):
        self.dialog_complete_event.clear()
        # Context for voice node
        context = {
            "child_name": self.interaction_spec.child_name,
            "task": self.interaction_spec.task_description,
            "activity_description": self.interaction_spec.activity_description,
            "activity_type": self.interaction_spec.activity_type,
        }
        payload =json.dumps({"command": "start_child_activity","context": context})

        self.publish(self.dialog_command_pub,payload)
        self.publish_feedback(f"Starting child activity: {payload}")

        timeout_sec = float(self.get_parameter("child_activity_timeout_sec").value)

        if timeout_sec <= 0:

            timeout_sec = float(self.get_parameter("storytelling_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.0f}s for child activity to complete"
        )
        completed = self.dialog_complete_event.wait(
            timeout=timeout_sec if timeout_sec > 0 else None
        )
        if completed:
            self.publish_feedback(
                f"Child activity complete: {self.dialog_complete_payload}"
            )
        else:
            self.publish_feedback("Child activity timed out; stopping HRI", log_level="warn")

            self.publish(self.dialog_command_pub,json.dumps({"command": "stop"}))

    def propose_activity(self,memory=None):
        child_name = self.interaction_spec.child_name
        if memory is None:
            self.publish_feedback(f"No child memory found for {child_name}")
            return None

        activity_type = self.preferred_activity_from_memory(memory)

        if not activity_type:
            self.publish_feedback(f"No preferred activity in child memory for {child_name}")
            return None

        task = ACTIVITY_TO_TASK.get(activity_type,"")
        self.interaction_spec.activity_type = activity_type
        self.interaction_spec.task_description = task
        self.interaction_spec.activity_description = self.activity_label(activity_type)

        proposal = str(self.get_parameter("proposal_text").value).format(
            child_name=child_name,
            activity_label=self.activity_label(activity_type),
        )
        self.say(proposal)
        self.publish_feedback(
            f"Proposed {activity_type} from child memory for {child_name}"
        )
        return {
            "activity_type": activity_type,
            "task_description": task,
            "source": "child_memory",
        }

    def child_memory_for_activity_proposal(self, child_name):
        memory_store = self.get_child_memory_store()
        if memory_store is None:
            return None
        try:
            memory = memory_store.get_child_memory_context(
                child_name=child_name,
                activity_type=str(self.get_parameter("default_activity_type").value),
                max_facts=12,
            )
        except Exception as exc:
            self.publish_feedback(
                f"Could not read child memory for activity proposal: {exc}",
                log_level="warn",
            )
            return None
        if not memory.get("found"):
            return None
        canonical_name = str(memory.get("child_name", "")).strip()
        if canonical_name:
            self.interaction_spec.child_name = canonical_name
            self.child_name = canonical_name
        return memory

    def get_child_memory_store(self):
        if self.child_memory_store is not None:
            return self.child_memory_store
        if RdfChildMemoryStore is None:
            self.publish_feedback(
                "RDF child memory is unavailable; cannot propose from memory",
                log_level="warn",
            )
            return None
        rdf_path = str(self.get_parameter("child_memory_rdf_path").value).strip()

        if not rdf_path:
            self.publish_feedback(
                "child_memory_rdf_path is empty; cannot propose from memory",
                log_level="warn",
            )
            return None
        try:
            self.child_memory_store = RdfChildMemoryStore(
                rdf_path=rdf_path,
                seed_if_missing=bool(
                    self.get_parameter("child_memory_seed_if_missing").value
                ),
            )
        except Exception as exc:
            self.publish_feedback(
                f"Failed to initialize child memory at {rdf_path}: {exc}",
                log_level="warn",
            )
            self.child_memory_store = None

        return self.child_memory_store

    def preferred_activity_from_memory(self,memory):
        for activity in memory.get("preferred_activity_types",[]):

            normalized= self.normalize_memory_activity_type(activity)
            if normalized:
                return normalized
        return ""

    def normalize_memory_activity_type(self, activity):
        normalized = activity.strip().lower().replace("-", "_").replace(" ","_")
        if normalized in ACTIVITY_TO_TASK:
            return normalized
        inferred = infer_activity_and_task(activity)
        if inferred:
            return inferred[0]
        return ""

    def wait_for_proposal_response(self):
        timeout_sec =float(self.get_parameter("proposal_acceptance_timeout_sec").value)
        self.override_event.clear()
        self.override_text = ""
        self.publish_feedback(
            f"Waiting {timeout_sec:.0f}s for therapist yes/no response to proposal"
        )
        if self.override_event.wait(timeout=timeout_sec):
            return self.parse_proposal_response(self.override_text)
        return None

    @staticmethod
    def parse_proposal_response(text):
        normalized = text.strip().lower()
        if not normalized:
            return None
        tokens = {
            token.strip(".,!?;:")
            for token in normalized.split()
            if token.strip(".,!?;:")
        }
        yes_tokens = {
            "yes","yeah", "yep","ok", "okay","sure","fine", "correct", "si","sì",
        }
        no_tokens = {
            "no","nope", "not","don't", "different","change","cambia",
            "cambiamo", "diverso","diversa",
        }
        yes_phrases = {"va bene"}
        no_phrases = {"do not"}
        if tokens & no_tokens or any(phrase in normalized for phrase in no_phrases):

            return "no"
        if tokens & yes_tokens or any(phrase in normalized for phrase in yes_phrases):
            return "yes"
        return None

    def wait_for_therapist_override(self):
        timeout_sec = float(self.get_parameter("proposal_acceptance_timeout_sec").value)
        self.override_event.clear()
        self.override_text = ""
        self.publish_feedback(
            f"Waiting {timeout_sec:.0f}s for therapist override"
        )
        if self.override_event.wait(timeout=timeout_sec):
            return self.override_text.strip()
        return None

    def apply_therapist_override(self, text):
        inferred = infer_activity_and_task(text)
        if inferred:
            activity_type,task = inferred
            self.interaction_spec.activity_type = activity_type
            self.interaction_spec.task_description = task
            self.interaction_spec.activity_description = text
        else:

            self.interaction_spec.activity_type = self.normalize_activity_type(text)
            task = ACTIVITY_TO_TASK.get(self.interaction_spec.activity_type,"")
            if task:
                self.interaction_spec.task_description = task
            self.interaction_spec.activity_description = text

    @staticmethod
    def activity_label(activity_type):
        labels = {
            "storytelling": "storytelling with the book",
            "mood_check_teddy": "a mood check with the teddy bear",
            "computer_emotion_recognition": "emotion recognition with the computer",
            "generic_child_activity": "a fun activity",
        }
        return labels.get(activity_type, "a fun activity")

    def build_activity_summary(self):
        try:
            payload = json.loads(self.dialog_complete_payload)
        except (json.JSONDecodeError,TypeError):
            return "The activity is complete."
        scores = payload.get("scores", {})
        child_name = payload.get("child_name", "the child")
        activity_type = payload.get("activity_type", "activity")

        overall= scores.get("overall","N/A")
        reason = payload.get("reason", "completed")

        return (
            f"The {activity_type} session with {child_name} is complete. "
            f"Reason: {reason}. Overall engagement score: {overall}."
        )

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
        state = payload.get("state","")
        if state == "READY":
            self.gaze_ready_event.set()
        elif state == "ERROR":
            self.gaze_error_message = payload.get("message", "unknown error")
            self.gaze_ready_event.set()

    def start_gaze_process(self):
        if not bool(self.get_parameter("gaze_estimation.enabled").value):
            self.publish_feedback("Gaze estimation disabled by parameter")

            return True

        camera_index = str(self.get_parameter("gaze_estimation.camera_index").value)
        device = str(self.get_parameter("gaze_estimation.device").value)
        show_window = str(self.get_parameter("gaze_estimation.show_window").value).lower()

        command = [
            "ros2","launch","gaze_estimation", "gaze_with_attention.launch.py",
            f"camera_index:={camera_index}",
            f"device:={device}",
            f"show_window:={show_window}",
        ]

        self.gaze_ready_event.clear()
        self.gaze_error_message = ""
        self.gaze_process, _ = self.start_process(command,self.gaze_process_label)

        # Ready from gaze node status
        timeout_sec=float(self.get_parameter("gaze_estimation.startup_timeout_sec").value)
        self.publish_feedback(
            f"Waiting up to {timeout_sec:.1f}s for gaze estimation to become ready"
        )

        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not self.stop_event.is_set():
            if self.gaze_ready_event.is_set():
                break
            if self.gaze_process.poll() is not None:
                self.publish_feedback(
                    f"Gaze estimation process exited early with code "
                    f"{self.gaze_process.returncode}",
                    log_level="error",
                )
                self.gaze_process = None

                return False
            if time.monotonic() >= deadline:
                self.publish_feedback(
                    "Gaze estimation startup timed out",log_level="error"
                )
                self.stop_gaze_process()
                return False
            if self.skip_mode:
                self.gaze_ready_event.wait(timeout=0.2)
            else:
                rclpy.spin_once(self, timeout_sec=0.1)

        if self.gaze_error_message:
            self.publish_feedback(
                f"Gaze estimation failed to start: {self.gaze_error_message}",
                log_level="error",
            )
            self.stop_gaze_process()
            return False

        self.publish_feedback("Gaze estimation is ready")
        return True

    def stop_gaze_process(self):
        if self.gaze_process is not None:
            self.stop_process(self.gaze_process, self.gaze_process_label)
            self.gaze_process = None

    def normalize_activity_type(self,activity_description):
        text = activity_description.lower()
        rules =[
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

    def navigate_to_pose(self,label,pose_values):
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
            self.publish_feedback(f"Nav2 rejected {label} goal",log_level="error")
            return False

        result_future=goal_handle.get_result_async()
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

    def load_static_scene_graph(self):
        path = str(self.get_parameter("static_scene_graph_path").value).strip()
        if not path:
            message = "static_scene_graph_path is empty"
            self.publish_feedback(message,log_level="error")
            raise RuntimeError(message)
        if not os.path.exists(path):
            message = f"Static scene graph not found at {path}"
            self.publish_feedback(message,log_level="error")

            raise RuntimeError(message)
        try:
            graph = StaticSceneGraph.from_json(path)
        except (OSError, StaticSceneGraphError, json.JSONDecodeError) as exc:
            message = f"Cannot load static scene graph {path}: {exc}"
            self.publish_feedback(message, log_level="error")
            raise RuntimeError(message) from exc
        if graph.frame_id != str(self.get_parameter("map_frame").value):
            message = (
                f"Static scene graph frame '{graph.frame_id}' differs from map_frame "
                f"'{self.get_parameter('map_frame').value}'"
            )
            self.publish_feedback(message, log_level="error")
            raise RuntimeError(message)
        self.publish_feedback(f"Loaded static scene graph: {path}")
        return graph

    def static_nav_pose(self,target, role):
        pose = self.static_scene_graph.resolve_nav_pose(target, role=role)
        if pose is None:
            raise RuntimeError(
                f"Static scene graph has no nav pose for target '{target}' role '{role}'"
            )
        return [pose["x"],pose["y"],pose["yaw"]]

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

    def run_capture(self,params_file):
        paths = self.capture_output_paths(params_file)

        capture_executable = (
            "multi_view_capture_node"
            if self.multi_view_enabled(params_file)
            else "scene_capture_node"
        )

        start_time = time.time()
        process,label = self.start_process(
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
            deadline = time.monotonic()+timeout_sec
            while rclpy.ok() and not self.stop_event.is_set():
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
            self.stop_process(process,label)

    def capture_output_paths(self,params_file):
        use_case = self.empower_param(params_file, "use_case",self.get_parameter("use_case").value)
        output_root = self.empower_param(
            params_file,
            "output_root",
            self.get_parameter("output_root").value,
        )
        if self.multi_view_enabled(params_file):
            output_subdir = self.empower_param(params_file,"multi_view.output_subdir","views")
            return [
                os.path.join(output_root,"empower_output",use_case,"views_manifest.json"),
                os.path.join(output_root,"empower_output",use_case, output_subdir, "view_000", "rgb.jpg"),
                os.path.join(output_root,"empower_output", use_case,output_subdir, "view_000", "depth.npy"),
                os.path.join(output_root, "empower_output", use_case, output_subdir, "view_000","camera_pose_map.json"),
            ]

        return [
            os.path.join(output_root,"empower_output", use_case,"scan.jpg"),
            os.path.join(output_root, "empower_output", use_case, "depth.npy"),
            os.path.join(output_root, "empower_output", use_case,"depth_pointcloud.pcd"),
            os.path.join(output_root, "empower_output",use_case,"camera_info.json"),
        ]

    def multi_view_enabled(self, params_file):
        return bool(self.empower_param(params_file,"multi_view.enabled", False))

    def empower_param(self,params_file,name, default):
        try:
            with open(params_file, "r",encoding="utf-8") as params_stream:
                data = yaml.safe_load(params_stream) or {}
        except OSError:
            return default
        for section in data.values():
            if not isinstance(section,dict):

                continue
            ros_parameters = section.get("ros__parameters",{})
            found,value = self.nested_param(ros_parameters,name)
            if found:
                return value

        return default

    def nested_param(self,ros_parameters,name):
        if name in ros_parameters:
            return True,ros_parameters[name]
        current = ros_parameters
        for part in str(name).split("."):
            if not isinstance(current, dict) or part not in current:
                return False,None
            current = current[part]
        return True, current

    def files_ready(self, paths, start_time):
        for path in paths:
            if not os.path.exists(path):
                return False
            if os.path.getmtime(path) < start_time:
                return False

        return True

    def run_empower_execution(self,params_file):
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
            "-p",
            f"static_scene_graph_path:={self.get_parameter('static_scene_graph_path').value}",
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
            for process,label in reversed(helpers):
                self.stop_process(process,label)

    def start_process(self, command,label):
        self.publish_feedback(f"Starting {label}: {' '.join(command)}")
        return (
            subprocess.Popen(command, start_new_session=True),
            label,
        )

    def stop_process(self, process,label):
        if process.poll() is not None:
            return
        self.publish_feedback(f"Stopping {label}")
        try:
            os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=5.0)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5.0)
            except ProcessLookupError:
                return

    def run_checked(self,command,label):
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
        timeout =float(self.get_parameter("empower_process_timeout_sec").value)
        return timeout if timeout > 0.0 else None

    def use_sim_time_value(self):
        return str(bool(self.get_parameter("use_sim_time").value)).lower()

    def wait_for_future(self,future,timeout_sec, description):
        deadline = time.monotonic()+timeout_sec
        while rclpy.ok() and not self.stop_event.is_set():
            if future.done():
                return True
            if time.monotonic() >= deadline:
                self.publish_feedback(f"Timed out while {description}",log_level="error")
                return False
            time.sleep(0.05)
        return False

    def make_pose_stamped(self, values):
        x, y, yaw = values
        qz = math.sin(yaw*0.5)
        qw = math.cos(yaw * 0.5)
        pose =PoseStamped()
        pose.header.frame_id = str(self.get_parameter("map_frame").value)
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x

        pose.pose.position.y = y
        pose.pose.position.z = 0.0
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw
        return pose

    def transition(self,state):
        with self.state_lock:
            self.state = state
        self.publish(self.state_pub,state.value)
        self.get_logger().info(f"Mission state: {state.value}")

    def current_state(self):
        with self.state_lock:
            return self.state

    def publish_feedback(self,text,log_level="info"):
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

    def publish(self,publisher,text):
        msg = String()
        msg.data = str(text)
        publisher.publish(msg)

    def goal_status_name(self,status):
        names ={
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
        self.stop_event.set()
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

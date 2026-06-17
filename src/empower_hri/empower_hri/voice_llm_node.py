"""ROS2 node for a minimal local STT -> LLM -> TTS dialogue loop."""

import json
import threading
from collections import deque
from typing import Any, Deque, Dict, List

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from empower_hri.ollama_client import LlmError, MockLlmClient, OllamaClient
from empower_hri.speech_to_text import (
    FasterWhisperSpeechToText,
    MockSpeechToText,
    SpeechToTextError,
)
from empower_hri.terminal_io import read_terminal
from empower_hri.text_to_speech import TextToSpeech, TextToSpeechError

_MODE_THERAPIST = "therapist_command"
_MODE_STORYTELLING = "child_storytelling"

DEFAULT_SYSTEM_PROMPT = (
    "You are TIAGo, a social robot in a Gazebo simulation. At this stage, you can "
    "only speak with the user. You cannot move, grasp objects, recognize people, or "
    "perform physical actions from this module. Always answer in English in a short, "
    "clear, kind manner suitable for children and therapists. If the user asks you to "
    "perform a physical action, explain that you are currently in dialogue-only mode. "
    "Do not claim robotic capabilities that have not been implemented."
)

STORYTELLING_SYSTEM_PROMPT_TEMPLATE = (
    "You are TIAGo, a friendly social robot in a therapy session with a young child. "
    "You have just completed the following task: '{task}'. "
    "Your role is to tell an engaging, age-appropriate, imaginative story and actively "
    "involve the child through warm encouragement and simple questions. "
    "Rules: keep each response to 2-4 short sentences; be playful and enthusiastic; "
    "maintain strict story continuity across turns — always remember what was said before. "
    "When you receive the message [START_STORY], introduce yourself warmly, begin the "
    "story with an exciting opening scene, and end with a simple question for the child. "
    "In all subsequent turns, react naturally to the child's answer, advance the story, "
    "and close with a new question to keep them engaged. "
    "Respond only in English."
)


class VoiceLlmNode(Node):
    """Coordinate terminal/audio input, a local LLM, and host audio output."""

    def __init__(self):
        super().__init__("voice_llm_node")
        self._declare_parameters()
        self._validate_parameters()

        self.user_publisher = self.create_publisher(String, "/hri/user_utterance", 10)
        self.robot_publisher = self.create_publisher(String, "/hri/robot_utterance", 10)
        self.state_publisher = self.create_publisher(String, "/hri/dialog_state", 10)
        self.dialog_complete_pub = self.create_publisher(String, "/hri/dialog_complete", 10)

        self.create_subscription(String, "/hri/dialog_command", self._dialog_command_callback, 10)

        self.history: Deque[Dict[str, str]] = deque(
            maxlen=2 * int(self.get_parameter("max_history_turns").value)
        )
        self.llm = self._create_llm()
        self.stt = self._create_stt()
        self.tts = TextToSpeech(
            provider=str(self.get_parameter("tts_provider").value),
            piper_model_path=str(self.get_parameter("piper_model_path").value),
            piper_config_path=str(self.get_parameter("piper_config_path").value),
            language=str(self.get_parameter("language").value),
            timeout=float(self.get_parameter("tts_timeout").value),
            logger=self.get_logger(),
        )

        self._mode: str = _MODE_THERAPIST
        self._mode_lock = threading.Lock()
        self._story_context: Dict[str, Any] = {}
        self._storytelling_stop_event = threading.Event()

        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._interaction_loop,
            name="voice_llm_interaction",
            daemon=True,
        )
        self._worker.start()

    def _declare_parameters(self) -> None:
        self.declare_parameter("input_mode", "text")
        self.declare_parameter("llm_provider", "ollama")
        self.declare_parameter("llm_base_url", "http://localhost:11434")
        self.declare_parameter("llm_model", "qwen2.5:1.5b")
        self.declare_parameter("llm_fallback_model", "llama3.2:1b")
        self.declare_parameter("llm_timeout", 60.0)
        self.declare_parameter("temperature", 0.4)
        self.declare_parameter("max_response_tokens", 120)
        self.declare_parameter("max_history_turns", 4)
        self.declare_parameter("stt_provider", "faster_whisper")
        self.declare_parameter("stt_model", "tiny")
        self.declare_parameter("stt_device", "cpu")
        self.declare_parameter("stt_compute_type", "int8")
        self.declare_parameter("stt_device_index", -1)
        self.declare_parameter("language", "it")
        self.declare_parameter("record_seconds", 4.0)
        self.declare_parameter("sample_rate", 16000)
        self.declare_parameter("tts_provider", "pyttsx3")
        self.declare_parameter("tts_timeout", 30.0)
        self.declare_parameter("piper_model_path", "")
        self.declare_parameter("piper_config_path", "")
        self.declare_parameter("system_prompt", DEFAULT_SYSTEM_PROMPT)
        self.declare_parameter("storytelling_max_turns", 10)
        self.declare_parameter("storytelling_auto_listen", True)
        self.declare_parameter("stt_vad_energy_threshold", 0.01)
        self.declare_parameter("stt_vad_silence_sec", 1.5)
        self.declare_parameter("stt_vad_max_duration_sec", 30.0)

    def _validate_parameters(self) -> None:
        input_mode = str(self.get_parameter("input_mode").value)
        if input_mode not in {"text", "voice"}:
            raise ValueError("input_mode deve essere 'text' oppure 'voice'.")
        if int(self.get_parameter("max_history_turns").value) < 1:
            raise ValueError("max_history_turns deve essere almeno 1.")
        if int(self.get_parameter("max_response_tokens").value) < 1:
            raise ValueError("max_response_tokens deve essere almeno 1.")
        if float(self.get_parameter("tts_timeout").value) <= 0:
            raise ValueError("tts_timeout deve essere maggiore di zero.")
        if str(self.get_parameter("stt_device").value).lower() != "cpu":
            raise ValueError(
                "empower_hri è CPU-only: configura stt_device:=cpu."
            )

    def _create_llm(self):
        provider = str(self.get_parameter("llm_provider").value)
        if provider == "mock":
            return MockLlmClient()
        if provider == "ollama":
            return OllamaClient(
                base_url=str(self.get_parameter("llm_base_url").value),
                model=str(self.get_parameter("llm_model").value),
                fallback_model=str(
                    self.get_parameter("llm_fallback_model").value
                ),
                timeout=float(self.get_parameter("llm_timeout").value),
                logger=self.get_logger(),
            )
        raise ValueError(
            f"Provider LLM non supportato: '{provider}'. Usa ollama oppure mock."
        )

    def _create_stt(self):
        if str(self.get_parameter("input_mode").value) == "text":
            return None
        provider = str(self.get_parameter("stt_provider").value)
        if provider == "mock":
            return MockSpeechToText()
        if provider == "faster_whisper":
            return FasterWhisperSpeechToText(
                model_name=str(self.get_parameter("stt_model").value),
                device=str(self.get_parameter("stt_device").value),
                compute_type=str(self.get_parameter("stt_compute_type").value),
                language=str(self.get_parameter("language").value),
                record_seconds=float(self.get_parameter("record_seconds").value),
                sample_rate=int(self.get_parameter("sample_rate").value),
                device_index=int(self.get_parameter("stt_device_index").value),
                logger=self.get_logger(),
                state_callback=self._publish_state,
            )
        raise ValueError(
            f"Provider STT non supportato: '{provider}'. "
            "Usa faster_whisper oppure mock."
        )

    def _dialog_command_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().error(
                f"Invalid JSON in /hri/dialog_command: {msg.data}"
            )
            return
        command = payload.get("command", "")
        if command == "start_storytelling":
            with self._mode_lock:
                self._story_context = dict(payload.get("context", {}))
                self._storytelling_stop_event.clear()
                self.history.clear()
                self._mode = _MODE_STORYTELLING
            self.get_logger().info(
                f"Switched to storytelling mode. Context: {self._story_context}"
            )
        elif command == "stop":
            self._storytelling_stop_event.set()
            self.get_logger().info("Storytelling stop requested via /hri/dialog_command")
        else:
            self.get_logger().warning(f"Unknown dialog_command: '{command}'")

    def _current_system_prompt(self) -> str:
        with self._mode_lock:
            mode = self._mode
            context = dict(self._story_context)
        if mode == _MODE_STORYTELLING:
            task = context.get("task", "tell a story to a child")
            return STORYTELLING_SYSTEM_PROMPT_TEMPLATE.format(task=task)
        return str(self.get_parameter("system_prompt").value)

    def _publish(self, publisher, text: str) -> None:
        message = String()
        message.data = text
        publisher.publish(message)

    def _publish_state(self, state: str) -> None:
        self._publish(self.state_publisher, state)
        self.get_logger().info(f"Stato dialogo: {state}")

    def _read_user_text(self) -> str:
        if str(self.get_parameter("input_mode").value) == "text":
            self._publish_state("waiting_for_text")
            return read_terminal("Tu (oppure /quit): ").strip()
        self._publish_state("waiting_for_push_to_talk")
        return self.stt.transcribe_push_to_talk()

    def _read_child_text(self) -> str:
        """Listen for child input. Uses auto-VAD in voice mode if enabled."""
        if (
            str(self.get_parameter("input_mode").value) == "voice"
            and bool(self.get_parameter("storytelling_auto_listen").value)
            and hasattr(self.stt, "transcribe_auto")
        ):
            return self.stt.transcribe_auto(
                energy_threshold=float(self.get_parameter("stt_vad_energy_threshold").value),
                silence_duration_sec=float(self.get_parameter("stt_vad_silence_sec").value),
                max_duration_sec=float(self.get_parameter("stt_vad_max_duration_sec").value),
            )
        return self._read_user_text()

    def _messages(self, user_text: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": self._current_system_prompt()},
            *list(self.history),
            {"role": "user", "content": user_text},
        ]

    def _handle_utterance(self, user_text: str) -> None:
        self._publish(self.user_publisher, user_text)
        self.get_logger().info(f"Utente: {user_text}")
        self._publish_state("thinking")
        response = self.llm.chat(
            messages=self._messages(user_text),
            temperature=float(self.get_parameter("temperature").value),
            max_response_tokens=int(
                self.get_parameter("max_response_tokens").value
            ),
        )
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": response})
        self._publish(self.robot_publisher, response)
        self.get_logger().info(f"TIAGo: {response}")
        self._publish_state("speaking")
        backend = self.tts.speak(response)
        self.get_logger().info(f"Risposta riprodotta con TTS '{backend}'.")

    def _run_therapist_turn(self) -> bool:
        """One STT/LLM/TTS cycle in therapist command mode. Returns False to stop the loop."""
        try:
            user_text = self._read_user_text()
            if user_text.lower() in {"/quit", "/exit", "quit", "exit"}:
                self.get_logger().info("Chiusura richiesta dal terminale.")
                return False
            if not user_text:
                self.get_logger().warning("Nessun testo rilevato; riprova.")
                return True
            self._handle_utterance(user_text)
            self._publish_state("ready")
        except EOFError:
            self.get_logger().info("Input terminale chiuso.")
            return False
        except KeyboardInterrupt:
            return False
        except (LlmError, SpeechToTextError, TextToSpeechError) as exc:
            self._publish_state("error")
            self.get_logger().error(str(exc))
        except Exception as exc:
            self._publish_state("error")
            self.get_logger().error(
                f"Errore inatteso nel ciclo HRI: {type(exc).__name__}: {exc}"
            )
        return True

    def _run_storytelling_session(self) -> None:
        """Multi-turn interactive storytelling loop with the child."""
        self.get_logger().info("Starting storytelling session with child")
        self._publish_state("storytelling_active")

        # Robot generates the story opening without waiting for child input
        try:
            self._publish_state("thinking")
            opening = self.llm.chat(
                messages=[
                    {"role": "system", "content": self._current_system_prompt()},
                    {"role": "user", "content": "[START_STORY]"},
                ],
                temperature=float(self.get_parameter("temperature").value),
                max_response_tokens=int(self.get_parameter("max_response_tokens").value),
            )
            self.history.append({"role": "user", "content": "[START_STORY]"})
            self.history.append({"role": "assistant", "content": opening})
            self._publish(self.robot_publisher, opening)
            self.get_logger().info(f"TIAGo (story opening): {opening}")
            self._publish_state("speaking")
            self.tts.speak(opening)
        except (LlmError, TextToSpeechError) as exc:
            self.get_logger().error(f"Story opening failed: {exc}")

        # Interactive turns: child speaks, robot reacts and continues story
        max_turns = int(self.get_parameter("storytelling_max_turns").value)
        turns = 0

        while (
            rclpy.ok()
            and not self._stop_event.is_set()
            and not self._storytelling_stop_event.is_set()
            and (max_turns <= 0 or turns < max_turns)
        ):
            try:
                child_text = self._read_child_text()
                if child_text.lower() in {"/quit", "/exit", "quit", "exit"}:
                    break
                if not child_text:
                    # VAD: no speech detected within max_duration — keep listening
                    continue
                self._handle_utterance(child_text)
                turns += 1
                self._publish_state("ready")
            except EOFError:
                break
            except KeyboardInterrupt:
                break
            except (LlmError, SpeechToTextError, TextToSpeechError) as exc:
                self._publish_state("error")
                self.get_logger().error(f"Storytelling turn {turns} error: {exc}")
            except Exception as exc:
                self._publish_state("error")
                self.get_logger().error(
                    f"Unexpected error in storytelling turn {turns}: "
                    f"{type(exc).__name__}: {exc}"
                )

        reason = "max_turns" if (max_turns > 0 and turns >= max_turns) else "stopped"
        self.get_logger().info(
            f"Storytelling session ended: {turns} turns, reason={reason}"
        )
        self._publish(
            self.dialog_complete_pub,
            json.dumps({"reason": reason, "turns": turns}),
        )
        self._publish_state("ready")
        with self._mode_lock:
            self._mode = _MODE_THERAPIST

    def _interaction_loop(self) -> None:
        input_mode = str(self.get_parameter("input_mode").value)
        self.get_logger().info(
            f"HRI locale pronto: input={input_mode}, "
            f"LLM={self.get_parameter('llm_provider').value}, "
            f"TTS={self.get_parameter('tts_provider').value}."
        )
        self._publish_state("ready")
        while rclpy.ok() and not self._stop_event.is_set():
            with self._mode_lock:
                current_mode = self._mode
            if current_mode == _MODE_STORYTELLING:
                self._run_storytelling_session()
            else:
                if not self._run_therapist_turn():
                    break
        self._publish_state("stopped")
        self._stop_event.set()
        if rclpy.ok():
            rclpy.shutdown()

    def destroy_node(self):
        self._stop_event.set()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = VoiceLlmNode()
        rclpy.spin(node)
    except (ValueError, RuntimeError) as exc:
        if node is not None:
            node.get_logger().error(str(exc))
        else:
            print(f"Errore di configurazione empower_hri: {exc}")
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

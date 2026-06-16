"""ROS2 node for a minimal local STT -> LLM -> TTS dialogue loop."""

import threading
from collections import deque
from typing import Deque, Dict, List

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


DEFAULT_SYSTEM_PROMPT = (
    "Sei TIAGo, un robot sociale in simulazione Gazebo. In questa fase puoi "
    "solo parlare con l'utente tramite voce. Non puoi ancora muoverti, "
    "afferrare oggetti, riconoscere persone o eseguire azioni fisiche da "
    "questo modulo. Rispondi sempre in italiano, in modo breve, chiaro, "
    "gentile e adatto a un'interazione con bambini e terapeuti. Se l'utente "
    "ti chiede di fare qualcosa fisicamente, spiega che per ora stai "
    "testando solo il dialogo vocale. Non inventare capacità robotiche non "
    "ancora implementate."
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
        self.declare_parameter("language", "it")
        self.declare_parameter("record_seconds", 4.0)
        self.declare_parameter("sample_rate", 16000)
        self.declare_parameter("tts_provider", "pyttsx3")
        self.declare_parameter("tts_timeout", 30.0)
        self.declare_parameter("piper_model_path", "")
        self.declare_parameter("piper_config_path", "")
        self.declare_parameter("system_prompt", DEFAULT_SYSTEM_PROMPT)

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
                logger=self.get_logger(),
                state_callback=self._publish_state,
            )
        raise ValueError(
            f"Provider STT non supportato: '{provider}'. "
            "Usa faster_whisper oppure mock."
        )

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

    def _messages(self, user_text: str) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": str(self.get_parameter("system_prompt").value),
            },
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

    def _interaction_loop(self) -> None:
        input_mode = str(self.get_parameter("input_mode").value)
        self.get_logger().info(
            f"HRI locale pronto: input={input_mode}, "
            f"LLM={self.get_parameter('llm_provider').value}, "
            f"TTS={self.get_parameter('tts_provider').value}."
        )
        self._publish_state("ready")
        while rclpy.ok() and not self._stop_event.is_set():
            try:
                user_text = self._read_user_text()
                if user_text.lower() in {"/quit", "/exit", "quit", "exit"}:
                    self.get_logger().info("Chiusura richiesta dal terminale.")
                    break
                if not user_text:
                    self.get_logger().warning(
                        "Nessun testo rilevato; riprova."
                    )
                    continue
                self._handle_utterance(user_text)
                self._publish_state("ready")
            except EOFError:
                self.get_logger().info("Input terminale chiuso.")
                break
            except KeyboardInterrupt:
                break
            except (LlmError, SpeechToTextError, TextToSpeechError) as exc:
                self._publish_state("error")
                self.get_logger().error(str(exc))
            except Exception as exc:
                self._publish_state("error")
                self.get_logger().error(
                    f"Errore inatteso nel ciclo HRI: {type(exc).__name__}: {exc}"
                )
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

"""Push-to-talk recording and local speech transcription."""

import os
import tempfile
from typing import Callable, Optional

from empower_hri.terminal_io import read_terminal


class SpeechToTextError(RuntimeError):
    """Readable error raised by recording or transcription."""


class MockSpeechToText:
    """Manual transcription provider used when audio is unavailable."""

    def transcribe_push_to_talk(self) -> str:
        return read_terminal("Simulated transcription (or /quit): ").strip()

    def transcribe_auto(self, **kwargs) -> str:
        return read_terminal("Simulated auto-listening (or /quit): ").strip()


class FasterWhisperSpeechToText:
    """Record a short WAV and transcribe it locally on CPU."""

    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        language: str,
        record_seconds: float,
        sample_rate: int,
        device_index: int = -1,
        logger=None,
        state_callback: Optional[Callable[[str], None]] = None,
    ):
        if device.lower() != "cpu":
            raise SpeechToTextError(
                "This MVP supports CPU STT only. Set stt_device:=cpu."
            )
        self.model_name = model_name
        self.device = "cpu"
        self.compute_type = compute_type
        self.language = language
        self.record_seconds = record_seconds
        self.sample_rate = sample_rate
        self.device_index = device_index if device_index >= 0 else None
        self.logger = logger
        self.state_callback = state_callback
        self._model = None

    def _set_state(self, state: str) -> None:
        if self.state_callback is not None:
            self.state_callback(state)

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SpeechToTextError(
                "faster-whisper is not installed. Run: "
                "python3 -m pip install faster-whisper"
            ) from exc

        if self.logger is not None:
            self.logger.warning(
                f"Loading STT '{self.model_name}' on CPU/{self.compute_type}. "
                "If the model is not already cached, faster-whisper may download it now; "
                "see the README for preloading instructions."
            )
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            raise SpeechToTextError(
                f"Could not load faster-whisper model "
                f"'{self.model_name}': {exc}"
            ) from exc
        return self._model

    def _record_wav(self, wav_path: str) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise SpeechToTextError(
                "Recording requires sounddevice and soundfile. Run: "
                "python3 -m pip install sounddevice soundfile"
            ) from exc

        frame_count = int(self.record_seconds * self.sample_rate)
        if frame_count <= 0:
            raise SpeechToTextError("record_seconds must be greater than zero.")
        try:
            self._set_state("recording")
            if self.logger is not None:
                self.logger.info(
                    f"Recording from microphone for {self.record_seconds:.1f} seconds..."
                )
            audio = sd.rec(
                frame_count,
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device_index,
            )
            sd.wait()
            sf.write(wav_path, audio, self.sample_rate, subtype="PCM_16")
        except Exception as exc:
            raise SpeechToTextError(
                f"Microphone unavailable or recording failed: {exc}. "
                "Try input_mode:=text or stt_provider:=mock."
            ) from exc

    def transcribe_push_to_talk(self) -> str:
        command = read_terminal(
            "Press ENTER to record (or type /quit): "
        ).strip()
        if command:
            return command
        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
            self._record_wav(wav_path)
            self._set_state("transcribing")
            model = self._load_model()
            segments, _ = model.transcribe(
                wav_path,
                language=self.language or None,
                beam_size=1,
                vad_filter=True,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except SpeechToTextError:
            raise
        except Exception as exc:
            raise SpeechToTextError(f"STT transcription failed: {exc}") from exc
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

    def transcribe_auto(
        self,
        energy_threshold: float = 0.01,
        silence_duration_sec: float = 1.5,
        max_duration_sec: float = 30.0,
        min_speech_sec: float = 0.3,
    ) -> str:
        """Listen continuously and record when speech is detected via energy VAD.

        Returns empty string if no speech was detected within max_duration_sec.
        """
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise SpeechToTextError(
                "Automatic recording requires sounddevice, soundfile, and numpy."
            ) from exc

        chunk_sec = 0.05  # 50 ms per chunk for responsive VAD
        chunk_frames = int(self.sample_rate * chunk_sec)
        silence_chunks_needed = max(1, int(silence_duration_sec / chunk_sec))
        max_chunks = int(max_duration_sec / chunk_sec)
        min_speech_chunks = max(1, int(min_speech_sec / chunk_sec))

        recorded_chunks: list = []
        speech_started = False
        silence_count = 0
        speech_chunk_count = 0

        self._set_state("listening")
        if self.logger is not None:
            self.logger.info("Auto-listening... (speak when you are ready)")

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=chunk_frames,
                device=self.device_index,
            ) as stream:
                for _ in range(max_chunks):
                    chunk, _ = stream.read(chunk_frames)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))

                    if rms >= energy_threshold:
                        if not speech_started:
                            speech_started = True
                            self._set_state("recording")
                            if self.logger is not None:
                                self.logger.info("Voice detected, recording...")
                        silence_count = 0
                        speech_chunk_count += 1
                        recorded_chunks.append(chunk.copy())
                    elif speech_started:
                        recorded_chunks.append(chunk.copy())
                        silence_count += 1
                        if silence_count >= silence_chunks_needed:
                            break
        except Exception as exc:
            raise SpeechToTextError(
                f"Auto-listening failed: {exc}. "
                "Try storytelling_auto_listen:=false to return to push-to-talk."
            ) from exc

        if not speech_started or speech_chunk_count < min_speech_chunks:
            return ""

        wav_path = ""
        try:
            audio = np.concatenate(recorded_chunks, axis=0)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
            sf.write(wav_path, audio, self.sample_rate, subtype="PCM_16")
            self._set_state("transcribing")
            model = self._load_model()
            segments, _ = model.transcribe(
                wav_path,
                language=self.language or None,
                beam_size=1,
                vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except SpeechToTextError:
            raise
        except Exception as exc:
            raise SpeechToTextError(f"Automatic STT transcription failed: {exc}") from exc
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

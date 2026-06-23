#Push-to-talk recording and local speech transcription

import os
import tempfile
from typing import Callable, Optional

import numpy as np

from llm_interaction.terminal_io import read_terminal


def resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    
    if orig_sr == target_sr:
        return audio
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(indices, np.arange(len(audio)), audio.flatten()).astype(np.float32)


def get_device_samplerate(device_index, target_sr: int) -> int:
    
    try:
        import sounddevice as sd
        sd.check_input_settings(device=device_index, samplerate=target_sr)
        return target_sr
    except Exception:
        pass
    try:
        import sounddevice as sd
        info = sd.query_devices(device_index, "input")
        return int(info["default_samplerate"])
    except Exception:
        return target_sr


class SpeechToTextError(RuntimeError):
    pass


class MockSpeechToText:
    

    def transcribe_push_to_talk(self) -> str:
        return read_terminal("Simulated transcription (or /quit): ").strip()

    def transcribe_auto(self, **kwargs) -> str:
        return read_terminal("Simulated auto-listening (or /quit): ").strip()


class FasterWhisperSpeechToText:
    #Record a short WAV and transcribe it locally on CPU

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

    def set_state(self, state: str) -> None:
        if self.state_callback is not None:
            self.state_callback(state)

    def load_model(self):
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

    def record_wav(self, wav_path: str) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise SpeechToTextError(
                "Recording requires sounddevice and soundfile. Run: "
                "python3 -m pip install sounddevice soundfile"
            ) from exc

        hw_rate = get_device_samplerate(self.device_index, self.sample_rate)
        frame_count = int(self.record_seconds * hw_rate)
        if frame_count <= 0:
            raise SpeechToTextError("record_seconds must be greater than zero.")
        try:
            self.set_state("recording")
            if self.logger is not None:
                self.logger.info(
                    f"Recording from microphone for {self.record_seconds:.1f} seconds "
                    f"(hw_rate={hw_rate}, target={self.sample_rate})..."
                )
            audio = sd.rec(
                frame_count,
                samplerate=hw_rate,
                channels=1,
                dtype="float32",
                device=self.device_index,
            )
            sd.wait()
            if hw_rate != self.sample_rate:
                audio = resample(audio.flatten(), hw_rate, self.sample_rate)
            else:
                audio = audio.flatten()
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
            self.record_wav(wav_path)
            self.set_state("transcribing")
            model = self.load_model()
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
        
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise SpeechToTextError(
                "Automatic recording requires sounddevice, soundfile, and numpy."
            ) from exc

        hw_rate = get_device_samplerate(self.device_index, self.sample_rate)
        chunk_sec = 0.05  # 50 ms per chunk for responsive VAD
        chunk_frames = int(hw_rate * chunk_sec)
        silence_chunks_needed = max(1, int(silence_duration_sec / chunk_sec))
        max_chunks = int(max_duration_sec / chunk_sec)
        min_speech_chunks = max(1, int(min_speech_sec / chunk_sec))

        recorded_chunks: list = []
        speech_started = False
        silence_count = 0
        speech_chunk_count = 0

        self.set_state("listening")
        if self.logger is not None:
            self.logger.info(
                f"Auto-listening (hw_rate={hw_rate}, target={self.sample_rate})..."
            )

        try:
            with sd.InputStream(
                samplerate=hw_rate,
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
                            self.set_state("recording")
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
            audio = np.concatenate(recorded_chunks, axis=0).flatten()
            if hw_rate != self.sample_rate:
                audio = resample(audio, hw_rate, self.sample_rate)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
            sf.write(wav_path, audio, self.sample_rate, subtype="PCM_16")
            self.set_state("transcribing")
            model = self.load_model()
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

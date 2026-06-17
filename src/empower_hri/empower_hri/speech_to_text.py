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
        return read_terminal("Trascrizione simulata (oppure /quit): ").strip()


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
                "Questo MVP supporta solo STT CPU. Imposta stt_device:=cpu."
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
                "faster-whisper non è installato. Esegui: "
                "python3 -m pip install faster-whisper"
            ) from exc

        if self.logger is not None:
            self.logger.warning(
                f"Caricamento STT '{self.model_name}' su CPU/{self.compute_type}. "
                "Se il modello non è già in cache, faster-whisper potrebbe "
                "scaricarlo ora; consulta il README per precaricarlo."
            )
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            raise SpeechToTextError(
                f"Impossibile caricare il modello faster-whisper "
                f"'{self.model_name}': {exc}"
            ) from exc
        return self._model

    def _record_wav(self, wav_path: str) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise SpeechToTextError(
                "Per registrare servono sounddevice e soundfile. Esegui: "
                "python3 -m pip install sounddevice soundfile"
            ) from exc

        frame_count = int(self.record_seconds * self.sample_rate)
        if frame_count <= 0:
            raise SpeechToTextError("record_seconds deve essere maggiore di zero.")
        try:
            self._set_state("recording")
            if self.logger is not None:
                self.logger.info(
                    f"Registrazione dal microfono per {self.record_seconds:.1f} secondi..."
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
                f"Microfono non disponibile o registrazione fallita: {exc}. "
                "Prova input_mode:=text oppure stt_provider:=mock."
            ) from exc

    def transcribe_push_to_talk(self) -> str:
        command = read_terminal(
            "Premi INVIO per registrare (oppure digita /quit): "
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
            raise SpeechToTextError(f"Trascrizione STT fallita: {exc}") from exc
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

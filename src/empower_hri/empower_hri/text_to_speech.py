"""Local text-to-speech providers with graceful fallbacks."""

import os
import shutil
import subprocess
import tempfile
from typing import Callable, List, Optional


class TextToSpeechError(RuntimeError):
    """Readable error raised when no requested TTS backend can speak."""


class TextToSpeech:
    """Speak through Piper, pyttsx3, system tools, or terminal output."""

    def __init__(
        self,
        provider: str,
        piper_model_path: str = "",
        piper_config_path: str = "",
        language: str = "it",
        timeout: float = 30.0,
        logger=None,
    ):
        self.provider = provider
        self.piper_model_path = os.path.expanduser(piper_model_path)
        self.piper_config_path = os.path.expanduser(piper_config_path)
        self.language = language
        self.timeout = timeout
        self.logger = logger

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _warn(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _speak_piper(self, text: str) -> None:
        executable = shutil.which("piper")
        if executable is None:
            raise TextToSpeechError("Il comando 'piper' non è installato.")
        if not self.piper_model_path or not os.path.isfile(self.piper_model_path):
            raise TextToSpeechError(
                "piper_model_path non è configurato o il file non esiste."
            )
        if self.piper_config_path and not os.path.isfile(self.piper_config_path):
            raise TextToSpeechError("Il file piper_config_path non esiste.")

        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
            command = [
                executable,
                "--model",
                self.piper_model_path,
                "--output_file",
                wav_path,
            ]
            if self.piper_config_path:
                command.extend(["--config", self.piper_config_path])
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
            )
            self._play_wav(wav_path)
        except subprocess.TimeoutExpired as exc:
            raise TextToSpeechError(
                f"Piper non ha terminato entro {self.timeout:.0f} secondi."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise TextToSpeechError(f"Piper non ha generato l'audio: {detail}") from exc
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

    def _play_wav(self, wav_path: str) -> None:
        players = [
            ("aplay", ["aplay", "-q", wav_path]),
            ("paplay", ["paplay", wav_path]),
            ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path]),
        ]
        for executable, command in players:
            if shutil.which(executable):
                subprocess.run(command, check=True, timeout=self.timeout)
                return
        raise TextToSpeechError(
            "Piper ha creato il WAV, ma manca un player (aplay, paplay o ffplay)."
        )

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            import pyttsx3
        except ImportError as exc:
            raise TextToSpeechError(
                "pyttsx3 non è installato. Esegui: python3 -m pip install pyttsx3"
            ) from exc
        try:
            engine = pyttsx3.init()
            for voice in engine.getProperty("voices"):
                descriptor = " ".join(
                    [
                        str(getattr(voice, "id", "")),
                        str(getattr(voice, "name", "")),
                        str(getattr(voice, "languages", "")),
                    ]
                ).lower()
                if "ital" in descriptor or "it_" in descriptor or "it-" in descriptor:
                    engine.setProperty("voice", voice.id)
                    break
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            raise TextToSpeechError(f"pyttsx3 non riesce a riprodurre l'audio: {exc}") from exc

    def _speak_system(self, text: str) -> None:
        for executable in ("espeak-ng", "espeak"):
            executable_path = shutil.which(executable)
            if executable_path:
                self._info(f"TTS system: uso '{executable}'.")
                try:
                    subprocess.run(
                        [executable_path, "-v", self.language, text],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=self.timeout,
                    )
                    return
                except subprocess.TimeoutExpired as exc:
                    raise TextToSpeechError(
                        f"'{executable}' non ha terminato entro "
                        f"{self.timeout:.0f} secondi."
                    ) from exc
        if shutil.which("spd-say"):
            self._info("TTS system: uso 'spd-say'.")
            try:
                subprocess.run(
                    ["spd-say", "-l", self.language, "--wait", text],
                    check=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise TextToSpeechError(
                    f"'spd-say' non ha terminato entro {self.timeout:.0f} secondi."
                ) from exc
            return
        raise TextToSpeechError(
            "Né espeak-ng, espeak né spd-say sono disponibili."
        )

    @staticmethod
    def _speak_mock(text: str) -> None:
        print(f"TIAGo: {text}", flush=True)

    def speak(self, text: str) -> str:
        """Speak text, applying local fallbacks and returning the backend used."""
        providers: List[tuple[str, Callable[[str], None]]]
        if self.provider == "piper":
            providers = [
                ("piper", self._speak_piper),
                ("pyttsx3", self._speak_pyttsx3),
                ("system", self._speak_system),
                ("mock", self._speak_mock),
            ]
        elif self.provider == "pyttsx3":
            providers = [
                ("pyttsx3", self._speak_pyttsx3),
                ("system", self._speak_system),
                ("mock", self._speak_mock),
            ]
        elif self.provider == "system":
            providers = [
                ("system", self._speak_system),
                ("mock", self._speak_mock),
            ]
        elif self.provider == "mock":
            providers = [("mock", self._speak_mock)]
        else:
            raise TextToSpeechError(
                f"Provider TTS non supportato: '{self.provider}'. "
                "Usa piper, pyttsx3, system oppure mock."
            )

        failures = []
        for name, speak_function in providers:
            try:
                speak_function(text)
                if name != self.provider:
                    self._warn(f"TTS fallback attivo: uso '{name}'.")
                return name
            except (TextToSpeechError, subprocess.SubprocessError, OSError) as exc:
                failures.append(f"{name}: {exc}")
                self._warn(f"TTS '{name}' non disponibile: {exc}")
        raise TextToSpeechError("; ".join(failures))

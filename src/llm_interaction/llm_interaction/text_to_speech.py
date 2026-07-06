#Local text-to-speech providers"

import os
import shutil
import subprocess
import tempfile
import time


class TextToSpeechError(RuntimeError):
    pass


class TextToSpeech:


    def __init__(
        self,
        provider,
        piper_model_path = "",
        piper_config_path = "",
        language = "en",
        timeout = 30.0,
        logger=None,
    ):
        self.provider = provider
        self.piper_model_path = os.path.expanduser(piper_model_path)
        self.piper_config_path = os.path.expanduser(piper_config_path)
        self.language = language
        self.timeout = timeout
        self.logger = logger

    def info(self, message):
        if self.logger is not None:
            self.logger.info(message)

    def warn(self, message):
        if self.logger is not None:
            self.logger.warning(message)

    def speak_piper(self, text):
        executable = shutil.which("piper")
        if executable is None:
            raise TextToSpeechError("The 'piper' command is not installed.")
        if not self.piper_model_path or not os.path.isfile(self.piper_model_path):
            raise TextToSpeechError(
                "piper_model_path is not configured or the file does not exist."
            )
        if self.piper_config_path and not os.path.isfile(self.piper_config_path):
            raise TextToSpeechError("The piper_config_path file does not exist.")

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
            self.play_wav(wav_path)
        except subprocess.TimeoutExpired as exc:
            raise TextToSpeechError(
                f"Piper did not finish within {self.timeout:.0f} seconds."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise TextToSpeechError(f"Piper did not generate audio: {detail}") from exc
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

    def play_wav(self, wav_path):
        players = [
            ("paplay", "paplay", ["paplay", wav_path]),
            ("aplay pulse", "aplay", ["aplay", "-q", "-D", "pulse", wav_path]),
            ("aplay", "aplay", ["aplay", "-q", wav_path]),
            (
                "ffplay",
                "ffplay",
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path],
            ),
        ]
        failures = []
        available_player = False
        for label, executable, command in players:
            if not shutil.which(executable):
                continue
            available_player = True
            try:
                subprocess.run(
                    command,
                    check=True,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                )
                return
            except subprocess.TimeoutExpired as exc:
                failures.append(f"{label}: timed out after {self.timeout:.0f}s")
                self.warn(f"Audio player '{label}' timed out: {exc}")
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode(errors="replace").strip()
                if not detail:
                    detail = f"exit code {exc.returncode}"
                failures.append(f"{label}: {detail}")
                self.warn(f"Audio player '{label}' failed: {detail}")
            except OSError as exc:
                failures.append(f"{label}: {exc}")
                self.warn(f"Audio player '{label}' failed: {exc}")
        if available_player:
            raise TextToSpeechError(
                "Piper created the WAV, but playback failed: "
                + "; ".join(failures)
            )
        raise TextToSpeechError(
            "Piper created the WAV, but no player is available (aplay, paplay, or ffplay)."
        )

    def speak_pyttsx3(self, text):
        try:
            import pyttsx3
        except ImportError as exc:
            raise TextToSpeechError(
                "pyttsx3 is not installed. Run: python3 -m pip install pyttsx3"
            ) from exc
        try:
            engine = pyttsx3.init()
            language = (self.language or "").lower()
            if language.startswith("en"):
                preferred_markers = ("english", "en_", "en-")
            elif language.startswith("it"):
                preferred_markers = ("ital", "it_", "it-")
            else:
                preferred_markers = ()
            for voice in engine.getProperty("voices"):
                descriptor = " ".join(
                    [
                        str(getattr(voice, "id", "")),
                        str(getattr(voice, "name", "")),
                        str(getattr(voice, "languages", "")),
                    ]
                ).lower()
                if any(marker in descriptor for marker in preferred_markers):
                    engine.setProperty("voice", voice.id)
                    break
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            raise TextToSpeechError(f"pyttsx3 cannot play audio: {exc}") from exc

    def speak_system(self, text):
        for executable in ("espeak-ng", "espeak"):
            executable_path = shutil.which(executable)
            if executable_path:
                self.info(f"TTS system: using '{executable}'.")
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
                        f"'{executable}' did not finish within "
                        f"{self.timeout:.0f} seconds."
                    ) from exc
        if shutil.which("spd-say"):
            self.info("TTS system: using 'spd-say'.")
            try:
                subprocess.run(
                    ["spd-say", "-l", self.language, "--wait", text],
                    check=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise TextToSpeechError(
                    f"'spd-say' did not finish within {self.timeout:.0f} seconds."
                ) from exc
            return
        raise TextToSpeechError(
            "None of espeak-ng, espeak, or spd-say is available."
        )

    @staticmethod
    def speak_mock(text):
        print(f"TIAGo: {text}", flush=True)

    def speak(self, text):
        """Speak text, applying local fallbacks and returning the backend used."""
        if self.provider == "piper":
            providers = [
                ("piper", self.speak_piper),
                ("pyttsx3", self.speak_pyttsx3),
                ("system", self.speak_system),
                ("mock", self.speak_mock),
            ]
        elif self.provider == "pyttsx3":
            providers = [
                ("pyttsx3", self.speak_pyttsx3),
                ("system", self.speak_system),
                ("mock", self.speak_mock),
            ]
        elif self.provider == "system":
            providers = [
                ("system", self.speak_system),
                ("mock", self.speak_mock),
            ]
        elif self.provider == "mock":
            providers = [("mock", self.speak_mock)]
        else:
            raise TextToSpeechError(
                f"Unsupported TTS provider: '{self.provider}'. "
                "Use piper, pyttsx3, system, or mock."
            )

        failures = []
        for name, speak_function in providers:
            try:
                t0 = time.monotonic()
                speak_function(text)
                elapsed = time.monotonic() - t0
                self.info(f"TTS playback took {elapsed:.1f}s (backend='{name}')")
                if name != self.provider:
                    self.warn(f"TTS fallback active: using '{name}'.")
                return name
            except (TextToSpeechError, subprocess.SubprocessError, OSError) as exc:
                failures.append(f"{name}: {exc}")
                self.warn(f"TTS '{name}' unavailable: {exc}")
        raise TextToSpeechError("; ".join(failures))

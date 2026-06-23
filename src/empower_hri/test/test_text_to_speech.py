import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from empower_hri.text_to_speech import TextToSpeech


class TextToSpeechTest(unittest.TestCase):
    def test_mock_tts_prints_response(self):
        tts = TextToSpeech(provider="mock")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            backend = tts.speak("ciao")

        self.assertEqual(backend, "mock")
        self.assertIn("TIAGo: ciao", output.getvalue())

    @patch("empower_hri.text_to_speech.subprocess.run")
    @patch("empower_hri.text_to_speech.shutil.which")
    def test_system_tts_prefers_espeak_ng(self, which, run):
        which.side_effect = lambda command: (
            "/usr/bin/espeak-ng" if command == "espeak-ng" else None
        )
        tts = TextToSpeech(provider="system", timeout=12.0)

        self.assertEqual(tts.speak("ciao"), "system")
        run.assert_called_once_with(
            ["/usr/bin/espeak-ng", "-v", "en", "ciao"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=12.0,
        )

    @patch("empower_hri.text_to_speech.subprocess.run")
    @patch("empower_hri.text_to_speech.shutil.which")
    def test_piper_playback_prefers_paplay(self, which, run):
        which.side_effect = lambda command: (
            f"/usr/bin/{command}" if command in {"paplay", "aplay"} else None
        )
        tts = TextToSpeech(provider="piper", timeout=12.0)

        tts._play_wav("/tmp/test.wav")

        run.assert_called_once_with(
            ["paplay", "/tmp/test.wav"],
            check=True,
            stderr=subprocess.PIPE,
            timeout=12.0,
        )

    @patch("empower_hri.text_to_speech.subprocess.run")
    @patch("empower_hri.text_to_speech.shutil.which")
    def test_piper_playback_falls_back_after_player_failure(self, which, run):
        which.side_effect = lambda command: (
            f"/usr/bin/{command}" if command in {"paplay", "aplay"} else None
        )
        run.side_effect = [
            subprocess.CalledProcessError(
                1,
                ["paplay", "/tmp/test.wav"],
                stderr=b"Connection refused",
            ),
            None,
        ]
        tts = TextToSpeech(provider="piper", timeout=12.0)

        tts._play_wav("/tmp/test.wav")

        self.assertEqual(run.call_count, 2)
        run.assert_any_call(
            ["paplay", "/tmp/test.wav"],
            check=True,
            stderr=subprocess.PIPE,
            timeout=12.0,
        )
        run.assert_any_call(
            ["aplay", "-q", "-D", "pulse", "/tmp/test.wav"],
            check=True,
            stderr=subprocess.PIPE,
            timeout=12.0,
        )

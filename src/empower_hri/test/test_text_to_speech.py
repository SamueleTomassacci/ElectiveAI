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
            ["/usr/bin/espeak-ng", "-v", "it", "ciao"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=12.0,
        )

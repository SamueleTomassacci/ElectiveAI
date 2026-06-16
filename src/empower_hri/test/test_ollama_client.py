import unittest

from empower_hri.ollama_client import MockLlmClient, OllamaClient


class LlmClientTest(unittest.TestCase):
    def test_mock_client_mentions_latest_user_message(self):
        client = MockLlmClient()
        response = client.chat(
            [
                {"role": "user", "content": "prima frase"},
                {"role": "assistant", "content": "risposta"},
                {"role": "user", "content": "ciao robot"},
            ],
            temperature=0.4,
            max_response_tokens=120,
        )

        self.assertIn("ciao robot", response)

    def test_ollama_client_selects_installed_fallback(self):
        client = OllamaClient(
            "http://localhost:11434",
            "qwen2.5:1.5b",
            fallback_model="llama3.2:1b",
        )
        client._available_models = lambda: ["llama3.2:1b"]

        self.assertEqual(client._select_model(), "llama3.2:1b")

    def test_mock_client_uses_english_system_prompt(self):
        client = MockLlmClient()
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": "Always answer in English.",
                },
                {"role": "user", "content": "hello robot"},
            ],
            temperature=0.4,
            max_response_tokens=120,
        )

        self.assertIn("Hello!", response)
        self.assertIn("hello robot", response)

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

    def test_ollama_client_omits_num_predict_when_token_limit_is_zero(self):
        client = OllamaClient("http://localhost:11434", "qwen2.5:1.5b")
        captured = {}
        client._select_model = lambda: "qwen2.5:1.5b"

        def fake_request(path, payload=None):
            captured["path"] = path
            captured["payload"] = payload
            return {"message": {"content": "Hello there."}}

        client._request_json = fake_request

        response = client.chat(
            [{"role": "user", "content": "hello"}],
            temperature=0.4,
            max_response_tokens=0,
        )

        self.assertEqual(response, "Hello there.")
        self.assertNotIn("num_predict", captured["payload"]["options"])

    def test_ollama_client_sets_num_predict_when_token_limit_is_positive(self):
        client = OllamaClient("http://localhost:11434", "qwen2.5:1.5b")
        captured = {}
        client._select_model = lambda: "qwen2.5:1.5b"

        def fake_request(path, payload=None):
            captured["payload"] = payload
            return {"message": {"content": "Hello there."}}

        client._request_json = fake_request

        client.chat(
            [{"role": "user", "content": "hello"}],
            temperature=0.4,
            max_response_tokens=220,
        )

        self.assertEqual(captured["payload"]["options"]["num_predict"], 220)

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

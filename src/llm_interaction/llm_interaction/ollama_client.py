#Local LLM clients used by the HRI node

import json
from typing import Dict, List, Optional
from urllib import error, request


class LlmError(RuntimeError):
    pass


class OllamaClient:
    #Small dependency-free client for Ollama's local HTTP API

    def __init__(
        self,
        base_url: str,
        model: str,
        fallback_model: str = "",
        timeout: float = 60.0,
        logger=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.logger = logger

    def request_json(self, path: str, payload: Optional[Dict] = None) -> Dict:
        data = None
        headers = {"Accept": "application/json"}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"

        http_request = request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                pass
            raise LlmError(f"Ollama HTTP error ({exc.code}): {detail}") from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise LlmError(
                f"Could not contact Ollama at {self.base_url}: {reason}. "
                "Start the server with: ollama serve"
            ) from exc
        except TimeoutError as exc:
            raise LlmError(
                f"Ollama did not respond within {self.timeout:.0f} seconds."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LlmError("Ollama returned an invalid response.") from exc

    def available_models(self) -> List[str]:
        response = self.request_json("/api/tags")
        return [
            item.get("name", "")
            for item in response.get("models", [])
            if item.get("name")
        ]

    @staticmethod
    def matches_model(configured: str, available: str) -> bool:
        if configured == available:
            return True
        if ":" not in configured and available == f"{configured}:latest":
            return True
        if ":" not in available and configured == f"{available}:latest":
            return True
        return False

    def select_model(self) -> str:
        available = self.available_models()
        if any(self.matches_model(self.model, item) for item in available):
            return self.model

        if self.fallback_model and any(
            self.matches_model(self.fallback_model, item) for item in available
        ):
            if self.logger is not None:
                self.logger.warning(
                    f"Model '{self.model}' is not available; using fallback "
                    f"'{self.fallback_model}'."
                )
            return self.fallback_model

        raise LlmError(
            "Ollama is running, but the configured model does not seem available. "
            f"Run: ollama pull {self.model}"
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_response_tokens: int,
    ) -> str:
        """Generate one non-streaming response from the local model."""
        selected_model = self.select_model()
        options = {"temperature": temperature}
        if max_response_tokens > 0:
            options["num_predict"] = max_response_tokens
        response = self.request_json(
            "/api/chat",
            {
                "model": selected_model,
                "messages": messages,
                "stream": False,
                "options": options,
            },
        )
        content = response.get("message", {}).get("content", "").strip()
        if not content:
            raise LlmError("Ollama returned an empty response.")
        return content


class MockLlmClient:
    #Deterministic local provider for tests without Ollama

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_response_tokens: int,
    ) -> str:
        del temperature, max_response_tokens
        user_text = next(
            (
                message["content"]
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        return (
            f"Hello! I received: \"{user_text}\". "
            "At this stage I can talk with you, but I cannot perform "
            "physical actions."
        )

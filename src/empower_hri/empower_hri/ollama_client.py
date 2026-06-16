"""Local LLM clients used by the HRI node."""

import json
from typing import Dict, List, Optional
from urllib import error, request


class LlmError(RuntimeError):
    """Readable error raised by a local LLM provider."""


class OllamaClient:
    """Small dependency-free client for Ollama's local HTTP API."""

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

    def _request_json(self, path: str, payload: Optional[Dict] = None) -> Dict:
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
            raise LlmError(f"Errore HTTP da Ollama ({exc.code}): {detail}") from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise LlmError(
                f"Impossibile contattare Ollama su {self.base_url}: {reason}. "
                "Avvia il server con: ollama serve"
            ) from exc
        except TimeoutError as exc:
            raise LlmError(
                f"Ollama non ha risposto entro {self.timeout:.0f} secondi."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LlmError("Ollama ha restituito una risposta non valida.") from exc

    def _available_models(self) -> List[str]:
        response = self._request_json("/api/tags")
        return [
            item.get("name", "")
            for item in response.get("models", [])
            if item.get("name")
        ]

    @staticmethod
    def _matches_model(configured: str, available: str) -> bool:
        if configured == available:
            return True
        if ":" not in configured and available == f"{configured}:latest":
            return True
        if ":" not in available and configured == f"{available}:latest":
            return True
        return False

    def _select_model(self) -> str:
        available = self._available_models()
        if any(self._matches_model(self.model, item) for item in available):
            return self.model

        if self.fallback_model and any(
            self._matches_model(self.fallback_model, item) for item in available
        ):
            if self.logger is not None:
                self.logger.warning(
                    f"Modello '{self.model}' non disponibile; uso il fallback "
                    f"'{self.fallback_model}'."
                )
            return self.fallback_model

        raise LlmError(
            "Ollama è attivo ma il modello non sembra disponibile. "
            f"Esegui: ollama pull {self.model}"
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_response_tokens: int,
    ) -> str:
        """Generate one non-streaming response from the local model."""
        selected_model = self._select_model()
        response = self._request_json(
            "/api/chat",
            {
                "model": selected_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_response_tokens,
                },
            },
        )
        content = response.get("message", {}).get("content", "").strip()
        if not content:
            raise LlmError("Ollama ha restituito una risposta vuota.")
        return content


class MockLlmClient:
    """Deterministic local provider for tests without Ollama."""

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
        system_prompt = next(
            (
                message["content"]
                for message in messages
                if message.get("role") == "system"
            ),
            "",
        )
        if "answer in english" in system_prompt.lower():
            return (
                f"Hello! I received: \"{user_text}\". "
                "At this stage I can talk with you, but I cannot perform "
                "physical actions."
            )
        return (
            f"Ciao! Ho ricevuto: \"{user_text}\". "
            "In questa fase posso parlare con te, ma non eseguire azioni fisiche."
        )

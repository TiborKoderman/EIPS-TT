"""Ollama-backed answer generation for PA3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from prompts import build_with_context_messages, build_without_context_messages


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"


class GenerationError(RuntimeError):
    """Raised when the local Ollama generator cannot complete a request."""


@dataclass(frozen=True)
class GenerationConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    temperature: float = 0.0
    timeout_seconds: float = 180.0


class OllamaGenerator:
    """Small, deterministic client for the local Ollama chat endpoint."""

    def __init__(self, config: GenerationConfig | None = None) -> None:
        self.config = config or GenerationConfig()

    def answer_with_context(self, query: str, hits: list[Any]) -> str:
        return self._chat(build_with_context_messages(query, hits))

    def answer_without_context(self, query: str) -> str:
        return self._chat(build_without_context_messages(query))

    def _chat(self, messages: list[dict[str, str]]) -> str:
        endpoint = f"{self.config.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        try:
            response = requests.post(endpoint, json=payload, timeout=self.config.timeout_seconds)
        except requests.RequestException as exc:
            raise GenerationError(
                "Povezava z Ollamo ni uspela. Zazenite Ollama in prenesite model "
                f"z ukazom `ollama pull {self.config.model}`. Podrobnost: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise GenerationError(
                f"Ollama je vrnila neveljaven odgovor (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 400 or data.get("error"):
            detail = data.get("error", response.text)
            raise GenerationError(
                f"Ollama ni mogla uporabiti modela '{self.config.model}': {detail}. "
                f"Po potrebi izvedite `ollama pull {self.config.model}`."
            )

        message = data.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise GenerationError("Ollama je vrnila prazen odgovor.")
        return str(content).strip()

"""Ollama-Provider direkt via /api/chat."""
from __future__ import annotations

import json
from typing import Any

import requests

from src.models import ProviderConfig

from .base import BaseProvider


class OllamaProvider(BaseProvider):
    def __init__(self, name: str, config: ProviderConfig):
        super().__init__(name, config)
        if not self.config.base_url:
            raise ValueError(f"Ollama-Provider '{name}' benoetigt base_url")

    def chat(self, model: str, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 2000, **kwargs: Any) -> str:
        """Ruft Ollama /api/chat direkt auf."""
        # Modellname ohne ollama_chat/-Prefix
        clean_model = model.replace("ollama_chat/", "").replace("ollama/", "")

        payload = {
            "model": clean_model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False,
        }

        url = f"{self.config.base_url}/api/chat"
        try:
            r = requests.post(url, json=payload, timeout=180)
            r.raise_for_status()
            data = r.json()
            content = data.get("message", {}).get("content", "")
            return self._clean_content(content)
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Ollama-Timeout bei {url}")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Ollama nicht erreichbar unter {url}")
        except Exception as e:
            raise RuntimeError(f"Ollama-Call fehlgeschlagen: {e}")

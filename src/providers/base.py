"""Abstrakte Basisklasse fuer alle Provider."""
from __future__ import annotations

import abc
import os
from typing import Any

from src.models import ProviderConfig


class BaseProvider(abc.ABC):
    def __init__(self, name: str, config: ProviderConfig):
        self.name = name
        self.config = config
        self._api_key: str | None = None

    @property
    def api_key(self) -> str | None:
        if self.config.api_key_env and self._api_key is None:
            self._api_key = os.environ.get(self.config.api_key_env)
        return self._api_key

    @abc.abstractmethod
    def chat(self, model: str, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 2000, **kwargs: Any) -> str:
        """Fuehrt einen Chat-Call durch und gibt den Text-Content zurueck."""
        ...

    def _clean_content(self, text: str) -> str:
        """Entfernt Markdown-Code-Fences aus Modell-Antworten."""
        import re
        text = re.sub(r"^```(json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        return text.strip()

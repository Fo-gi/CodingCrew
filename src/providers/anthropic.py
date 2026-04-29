"""Anthropic-Provider via SDK oder HTTP."""
from __future__ import annotations

from typing import Any

from src.models import ProviderConfig

from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    def __init__(self, name: str, config: ProviderConfig):
        super().__init__(name, config)

    def chat(self, model: str, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 2000, **kwargs: Any) -> str:
        """Ruft Anthropic API auf - SDK bevorzugt, sonst HTTP-Fallback."""
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            anthropic_sdk = None

        if anthropic_sdk and self.api_key:
            return self._chat_sdk(anthropic_sdk, model, messages, temperature, max_tokens)
        else:
            return self._chat_http(model, messages, temperature, max_tokens)

    def _chat_sdk(self, sdk, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        client = sdk.Anthropic(api_key=self.api_key)
        # Modellname ohne anthropic/-Prefix
        clean_model = model.replace("anthropic/", "")
        resp = client.messages.create(
            model=clean_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return self._clean_content(resp.content[0].text)

    def _chat_http(self, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        import requests

        clean_model = model.replace("anthropic/", "")
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": clean_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        return self._clean_content(data["content"][0]["text"])

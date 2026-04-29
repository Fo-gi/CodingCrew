"""Provider-Factory fuer direkte Modell-Calls."""
from __future__ import annotations

from src.models import CrewConfig, ProviderType

from .anthropic import AnthropicProvider
from .base import BaseProvider
from .ollama import OllamaProvider


def get_provider(name: str, config: CrewConfig) -> BaseProvider:
    """Erstellt einen Provider anhand des Namens aus der Config."""
    provider_cfg = config.providers[name]
    if provider_cfg.type == ProviderType.ollama:
        return OllamaProvider(name, provider_cfg)
    elif provider_cfg.type == ProviderType.anthropic:
        return AnthropicProvider(name, provider_cfg)
    else:
        raise ValueError(f"Provider-Typ '{provider_cfg.type}' nicht implementiert")


def get_model_client(alias: str, config: CrewConfig) -> BaseProvider:
    """Gibt den Provider fuer ein Modell-Alias zurueck."""
    model_cfg = config.models[alias]
    return get_provider(model_cfg.provider, config)

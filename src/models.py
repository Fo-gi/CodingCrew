"""Pydantic-Modelle fuer die zentrale crew.yaml Config."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProviderType(str, Enum):
    ollama = "ollama"
    anthropic = "anthropic"
    openai = "openai"
    gemini = "gemini"


class ProviderConfig(BaseModel):
    type: ProviderType
    base_url: str | None = None
    api_key_env: str | None = None


class ModelConfig(BaseModel):
    provider: str
    model: str
    temperature: float = 0.1
    max_tokens: int = 2000


class AgentType(str, Enum):
    litellm = "litellm"
    claude_cli = "claude_cli"


class AgentConfig(BaseModel):
    model: str
    description: str
    tools: list[str] = Field(default_factory=list)
    prompt: str
    type: AgentType = AgentType.litellm


class TagConfig(BaseModel):
    name: str
    priority: int = 0
    handler: str | None = None
    color: str = "BFD4F2"


class LimitsConfig(BaseModel):
    max_iterations: int = 25
    task_budget_usd: float = 10.0
    daily_budget_usd: float = 20.0
    max_parallel: int = 1
    timeout_minutes: int = 240


class LiteLLMRouterConfig(BaseModel):
    num_retries: int = 2
    timeout: int = 180
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)


class LiteLLMBudgetConfig(BaseModel):
    max_budget: float = 20.0
    budget_duration: str = "1d"


class LiteLLMConfig(BaseModel):
    port: int = 4000
    host: str = "127.0.0.1"
    master_key_env: str = "LITELLM_MASTER_KEY"
    router: LiteLLMRouterConfig = Field(default_factory=LiteLLMRouterConfig)
    budget: LiteLLMBudgetConfig = Field(default_factory=LiteLLMBudgetConfig)


class GitHubConfig(BaseModel):
    repo: str
    auto_create_labels: bool = True


class CrewConfig(BaseModel):
    github: GitHubConfig
    tags: list[TagConfig] = Field(default_factory=list)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)

    @field_validator("models")
    @classmethod
    def validate_model_providers(cls, v: dict[str, ModelConfig], info: Any) -> dict[str, ModelConfig]:
        providers = info.data.get("providers", {})
        for alias, model in v.items():
            if model.provider not in providers:
                raise ValueError(f"Model '{alias}' references unknown provider '{model.provider}'")
        return v

    @field_validator("agents")
    @classmethod
    def validate_agent_models(cls, v: dict[str, AgentConfig], info: Any) -> dict[str, AgentConfig]:
        models = info.data.get("models", {})
        for name, agent in v.items():
            if agent.model not in models:
                raise ValueError(f"Agent '{name}' references unknown model '{agent.model}'")
        return v

    @classmethod
    def load(cls, path: Path | str = Path("crew.yaml")) -> CrewConfig:
        import yaml
        data = yaml.safe_load(Path(path).read_text())
        return cls(**data)

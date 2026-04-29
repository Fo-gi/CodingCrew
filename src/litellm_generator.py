#!/usr/bin/env python3
"""Generiert config/litellm.yaml aus crew.yaml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def generate(config) -> dict:
    """Erzeugt LiteLLM-Config-Dict aus CrewConfig."""
    model_list = []

    for alias, model in config.models.items():
        provider = config.providers[model.provider]

        entry = {
            "model_name": alias,
            "litellm_params": {
                "model": model.model,
            },
        }

        if provider.type.value == "ollama":
            entry["litellm_params"]["api_base"] = f"os.environ/{model.provider.upper()}_URL"
        elif provider.type.value == "anthropic":
            entry["litellm_params"]["api_key"] = f"os.environ/{provider.api_key_env}"
        elif provider.type.value == "openai":
            entry["litellm_params"]["api_key"] = f"os.environ/{provider.api_key_env}"
        elif provider.type.value == "gemini":
            entry["litellm_params"]["api_key"] = f"os.environ/{provider.api_key_env}"

        model_list.append(entry)

    router_settings = {
        "num_retries": config.litellm.router.num_retries,
        "timeout": config.litellm.router.timeout,
    }
    if config.litellm.router.fallbacks:
        router_settings["fallbacks"] = [
            {k: v} for k, v in config.litellm.router.fallbacks.items()
        ]

    litellm_settings = {
        "drop_params": True,
        "max_budget": config.litellm.budget.max_budget,
        "budget_duration": config.litellm.budget.budget_duration,
    }

    general_settings = {
        "master_key": f"os.environ/{config.litellm.master_key_env}",
    }

    return {
        "model_list": model_list,
        "router_settings": router_settings,
        "litellm_settings": litellm_settings,
        "general_settings": general_settings,
    }


def write_litellm_yaml(config, output_path: Path | str = "config/litellm.yaml") -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = generate(config)
    output.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return output


def main():
    parser = argparse.ArgumentParser(description="Generiere litellm.yaml aus crew.yaml")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--output", "-o", default="config/litellm.yaml", help="Ausgabe-Pfad")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from config import load_config

    cfg = load_config(args.config)
    path = write_litellm_yaml(cfg, args.output)
    print(f"LiteLLM-Config geschrieben nach: {path}")

    # Validierung
    loaded = yaml.safe_load(path.read_text())
    print(f"  Modelle: {len(loaded['model_list'])}")
    print(f"  Router-Fallbacks: {len(loaded.get('router_settings', {}).get('fallbacks', []))}")
    print(f"  Budget: ${loaded['litellm_settings']['max_budget']} / {loaded['litellm_settings']['budget_duration']}")


if __name__ == "__main__":
    main()

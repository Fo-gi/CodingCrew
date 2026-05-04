"""Multi-Projekt Config Loader."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.models import CrewConfig


CONFIG_DIR = Path.home() / "CodingCrew" / "configs"


def list_projects() -> list[str]:
    """List all available project configs."""
    if not CONFIG_DIR.exists():
        return []
    return [f.stem for f in CONFIG_DIR.glob("*.yaml") if f.stem != "default"]


def load_project_config(project_name: str) -> "CrewConfig":
    """Load config for specific project."""
    from src.models import CrewConfig

    config_file = CONFIG_DIR / f"{project_name}.yaml"
    if not config_file.exists():
        # Fallback to root crew.yaml
        root_config = Path.home() / "CodingCrew" / "crew.yaml"
        if root_config.exists():
            return CrewConfig.load(root_config)
        raise FileNotFoundError(f"Project config '{project_name}' not found")

    return CrewConfig.load(config_file)


def save_project_config(project_name: str, config: dict) -> Path:
    """Save config for specific project."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = CONFIG_DIR / f"{project_name}.yaml"
    config_file.write_text(yaml.dump(config, sort_keys=False, default_flow_style=False))
    return config_file

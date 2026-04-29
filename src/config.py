"""Config-Loader mit Defaults und CLI-Helper."""
from __future__ import annotations

import os
from pathlib import Path

from src.models import CrewConfig


def find_crew_yaml() -> Path:
    """Sucht crew.yaml im aktuellen Verzeichnis oder ~/CodingCrew/."""
    candidates = [
        Path("crew.yaml"),
        Path.home() / "CodingCrew" / "crew.yaml",
        Path.home() / "agent" / "crew.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("crew.yaml nicht gefunden. Suche in: " + ", ".join(str(c) for c in candidates))


def load_config(path: Path | str | None = None) -> CrewConfig:
    if path is None:
        path = find_crew_yaml()
    return CrewConfig.load(path)


def main():
    import json
    config = load_config()
    print(json.dumps(config.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    main()

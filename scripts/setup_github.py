#!/usr/bin/env python3
"""Legt GitHub-Repo und Labels aus crew.yaml an."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.models import CrewConfig
from src.github.setup import GitHubSetup


def load_config(config_path: str | None) -> CrewConfig:
    """Load config from YAML file."""
    import yaml
    path = Path(config_path) if config_path else Path("crew.yaml")
    return CrewConfig.load(path)


def main():
    parser = argparse.ArgumentParser(description="GitHub-Setup fuer CodingCrew")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts aendern")
    args = parser.parse_args()

    config = load_config(args.config)
    setup = GitHubSetup(config)

    print(f"Repo: {config.github.repo}")
    print(f"  auto_create_repo: {config.github.auto_create_repo}")
    print(f"  auto_create_labels: {config.github.auto_create_labels}")

    if args.dry_run:
        print("  [dry-run] Keine Aenderungen durchgefuehrt.")
        return

    setup.setup()
    print("Done.")


if __name__ == "__main__":
    main()

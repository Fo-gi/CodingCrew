#!/usr/bin/env python3
"""Legt GitHub-Labels aus crew.yaml an und prüft Repo-Existenz."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def gh(*args: str) -> str:
    """Führt gh aus und gibt stdout zurück."""
    r = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} fehlgeschlagen: {r.stderr}")
    return r.stdout


def repo_exists(repo: str) -> bool:
    r = subprocess.run(["gh", "repo", "view", repo], capture_output=True)
    return r.returncode == 0


def list_labels(repo: str) -> set[str]:
    try:
        out = gh("label", "list", "--repo", repo, "--json", "name")
        data = json.loads(out)
        return {item["name"] for item in data}
    except RuntimeError:
        return set()


def create_label(repo: str, name: str, color: str, description: str = "") -> None:
    r = subprocess.run(
        ["gh", "label", "create", name, "--repo", repo, "--color", color, "--description", description],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print(f"  + Label '{name}' angelegt")
    elif "already exists" in r.stderr.lower() or "name already exists" in r.stderr.lower():
        print(f"  = Label '{name}' existiert bereits")
    else:
        print(f"  ! Label '{name}' fehlgeschlagen: {r.stderr.strip()}")


def main():
    parser = argparse.ArgumentParser(description="GitHub-Setup für CodingCrew")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts ändern")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from config import load_config

    config = load_config(args.config)
    repo = config.github.repo

    print(f"Repo: {repo}")
    if not repo_exists(repo):
        print(f"  FEHLER: Repo '{repo}' existiert nicht!")
        print("  Erstelle es mit: gh repo create Fo-gi/... --public")
        sys.exit(1)
    print("  Repo existiert.")

    existing = list_labels(repo)
    print(f"  Vorhandene Labels: {len(existing)}")

    needed = {tag.name for tag in config.tags}
    missing = needed - existing

    if args.dry_run:
        print(f"  Fehlende Labels: {missing}")
        return

    if not missing:
        print("  Alle Labels vorhanden.")
        return

    print(f"  Lege {len(missing)} fehlende Labels an...")
    for tag in config.tags:
        if tag.name in missing:
            desc = f"CodingCrew {tag.name}"
            create_label(repo, tag.name, tag.color, desc)

    print("Done.")


if __name__ == "__main__":
    main()

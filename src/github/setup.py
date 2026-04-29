"""Automatisches GitHub-Setup: Repo + Labels."""
from __future__ import annotations

import json
import subprocess

from src.models import CrewConfig


class GitHubSetup:
    def __init__(self, config: CrewConfig):
        self.config = config
        self.repo = config.github.repo

    def _gh(self, *args: str) -> str:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh failed: {r.stderr}")
        return r.stdout

    def repo_exists(self) -> bool:
        r = subprocess.run(["gh", "repo", "view", self.repo], capture_output=True)
        return r.returncode == 0

    def create_repo(self) -> None:
        """Erstellt das Repo auf GitHub."""
        if self.repo_exists():
            print(f"Repo {self.repo} existiert bereits.")
            return
        parts = self.repo.split("/")
        if len(parts) != 2:
            raise ValueError(f"Ungueltiger Repo-Name: {self.repo} (erwartet: owner/name)")
        owner, name = parts
        print(f"Erstelle Repo {self.repo}...")
        self._gh("repo", "create", f"{owner}/{name}", "--public", "--add-readme")
        print(f"Repo {self.repo} erstellt.")

    def list_labels(self) -> set[str]:
        try:
            out = self._gh("label", "list", "--repo", self.repo, "--json", "name")
            data = json.loads(out)
            return {item["name"] for item in data}
        except RuntimeError:
            return set()

    def create_label(self, name: str, color: str, description: str = "") -> None:
        r = subprocess.run(
            ["gh", "label", "create", name, "--repo", self.repo, "--color", color, "--description", description],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"  + Label '{name}' angelegt")
        elif "already exists" in r.stderr.lower() or "name already exists" in r.stderr.lower():
            print(f"  = Label '{name}' existiert bereits")
        else:
            print(f"  ! Label '{name}' fehlgeschlagen: {r.stderr.strip()}")

    def setup_labels(self) -> None:
        """Legt alle fehlenden Labels aus der Config an."""
        existing = self.list_labels()
        needed = {tag.name for tag in self.config.tags}
        missing = needed - existing

        if not missing:
            print("Alle Labels vorhanden.")
            return

        print(f"Lege {len(missing)} fehlende Labels an...")
        for tag in self.config.tags:
            if tag.name in missing:
                desc = f"CodingCrew {tag.name}"
                self.create_label(tag.name, tag.color, desc)

    def setup(self) -> None:
        """Komplettes Setup: Repo + Labels."""
        if self.config.github.auto_create_repo:
            self.create_repo()
        if self.config.github.auto_create_labels:
            self.setup_labels()

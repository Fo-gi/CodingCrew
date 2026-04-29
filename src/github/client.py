"""GitHub-Client via gh CLI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class GitHubClient:
    def __init__(self, repo: str):
        self.repo = repo

    def _gh(self, *args: str) -> str:
        r = subprocess.run(
            ["gh", *args, "--repo", self.repo],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh failed: {r.stderr}")
        return r.stdout

    def list_issues(self, label: str, exclude: list[str] | None = None) -> list[dict]:
        search = " ".join(f"-label:{e}" for e in (exclude or []))
        out = self._gh(
            "issue", "list", "--label", label,
            "--search", search,
            "--json", "number,title,body,labels",
            "--limit", "50",
        )
        return json.loads(out)

    def edit_labels(self, num: int, add: list[str] | None = None, remove: list[str] | None = None) -> None:
        cmd = ["issue", "edit", str(num)]
        for a in add or []:
            cmd += ["--add-label", a]
        for r in remove or []:
            cmd += ["--remove-label", r]
        self._gh(*cmd)

    def create_pr(self, base: str, head: str, title: str, body: str) -> str:
        out = self._gh(
            "pr", "create",
            "--base", base, "--head", head,
            "--title", title,
            "--body", body,
        )
        return out.strip()

    def create_issue(self, title: str, body: str, labels: list[str]) -> None:
        cmd = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            cmd += ["--label", label]
        self._gh(*cmd)

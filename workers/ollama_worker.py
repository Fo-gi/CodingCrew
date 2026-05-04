"""Ollama Worker für lokale Modell-Ausführung."""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from jobqueue import Job
from .base import BaseWorker, WorkerState

if TYPE_CHECKING:
    from src.models import CrewConfig
    from src.providers.base import BaseProvider


class OllamaWorker(BaseWorker):
    """
    Worker der lokale Ollama-Modelle für Tasks nutzt.

    Unterstützt:
    - direct Agents (PO, Reviewer, QA) -> nur API-Call
    - Implementation (Junior Dev, Senior Dev) -> Worktree, Code generieren, Tests, Commit
    """

    def __init__(
        self,
        agent_name: str,
        config: CrewConfig,
        queue_dir: Optional[Path] = None,
        health_dir: Optional[Path] = None,
        workspace_base: Optional[Path] = None,
        worktree_base: Optional[Path] = None,
    ):
        super().__init__(
            worker_type=f"ollama-{agent_name}",
            queue_dir=queue_dir,
            health_dir=health_dir,
        )
        self.agent_name = agent_name
        self.config = config
        self.workspace_base = workspace_base or (Path.home() / "CodingCrew" / "workspace")
        self.worktree_base = worktree_base or (Path.home() / "CodingCrew" / "worktrees")

        self.agent_cfg = config.agents.get(agent_name)
        if not self.agent_cfg:
            raise ValueError(f"Unbekannter Agent: {agent_name}")

        self.model_cfg = config.models.get(self.agent_cfg.model)
        if not self.model_cfg:
            raise ValueError(f"Unbekanntes Modell: {self.agent_cfg.model}")

        from src.providers import get_model_client
        self.provider = get_model_client(self.agent_cfg.model, config)

        self._is_implementation = agent_name in ("junior_dev", "senior_dev")

    def _setup_worktree(self, project: str, issue_number: int, branch: str, body: str) -> Path:
        """Create or reuse worktree for issue."""
        wt = self.worktree_base / f"{project}/issue-{issue_number}"
        workspace = self.workspace_base / project

        if not workspace.exists():
            subprocess.run(
                ["gh", "repo", "clone", project, str(workspace)],
                check=True,
                capture_output=True,
            )

        r = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", branch],
            capture_output=True,
        )

        if wt.exists():
            pass
        elif r.returncode == 0:
            subprocess.run(
                ["git", "-C", str(workspace), "worktree", "add", "-B", branch, str(wt)],
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(workspace), "worktree", "prune"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "worktree", "add", "-B", branch, str(wt), "origin/main"],
                check=True,
                capture_output=True,
            )

        subprocess.run(["git", "config", "user.email", "agent@localhost"], cwd=wt, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Claude Agent"], cwd=wt, check=True, capture_output=True)

        (wt / "SPEC.md").write_text(body)
        return wt

    def _parse_code_blocks(self, content: str) -> list[dict]:
        """Parse markdown code blocks into file/shell actions.

        Returns list of dicts:
        - {'type': 'file', 'path': 'app.py', 'content': '...'}
        - {'type': 'shell', 'command': 'pip install ...'}
        """
        actions = []

        # Bereinige hauefige Ollama-Formatierungsfehler:
        # Manchmal fehlt das oeffnende ```, z.B.:
        #   python
        #   # file: app.py
        #   ...
        #   ```
        # Wir ersetzen solche Zeilen mit dem korrekten ```python
        cleaned = content
        for lang in ("python", "bash", "sh", "javascript", "js", "typescript", "ts",
                     "json", "yaml", "yml", "toml", "markdown", "md", "html", "css",
                     "rust", "go", "java", "c", "cpp", "sql", "dockerfile"):
            # Zeile die nur aus der Sprache besteht, gefolgt von nicht-leerer Zeile
            cleaned = re.sub(
                rf"^(?!```){lang}\s*\n(?=[^\n])",
                f"```{lang}\n",
                cleaned,
                flags=re.MULTILINE,
            )

        # Match fenced code blocks: ```lang\ncontent\n```
        pattern = re.compile(r"```(\w+)?\n(.*?)\n```", re.DOTALL)
        for match in pattern.finditer(cleaned):
            lang = (match.group(1) or "").strip().lower()
            block = match.group(2)
            lines = block.splitlines()
            if not lines:
                continue

            first_line = lines[0].strip()
            rest = "\n".join(lines[1:])

            if first_line.startswith("# file:"):
                file_path = first_line.replace("# file:", "").strip()
                actions.append({"type": "file", "path": file_path, "content": rest})
            elif first_line.startswith("# shell"):
                command = rest.strip()
                if command:
                    actions.append({"type": "shell", "command": command})
            elif lang in ("python", "javascript", "js", "typescript", "ts", "json",
                          "yaml", "yml", "toml", "md", "markdown", "html", "css", "sh", "bash"):
                # Fallback: try to detect file path from second line comment
                if len(lines) >= 2 and lines[1].strip().startswith("# file:"):
                    file_path = lines[1].strip().replace("# file:", "").strip()
                    content_rest = "\n".join(lines[2:])
                    actions.append({"type": "file", "path": file_path, "content": content_rest})

        return actions

    def _ensure_venv(self, wt: Path) -> Path | None:
        """Create virtual env in worktree if none exists. Returns venv bin path or None."""
        venv_path = wt / ".venv"
        if not venv_path.exists():
            self._log(f"Erstelle venv in {wt}")
            r = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                self._log(f"venv Erstellung fehlgeschlagen: {r.stderr}")
                return None
            # Ensure .venv and caches are in .gitignore
            gitignore = wt / ".gitignore"
            current = gitignore.read_text() if gitignore.exists() else ""
            additions = []
            if ".venv/" not in current and ".venv" not in current:
                additions.append(".venv/")
            if "__pycache__/" not in current and "__pycache__" not in current:
                additions.append("__pycache__/")
            if ".pytest_cache/" not in current and ".pytest_cache" not in current:
                additions.append(".pytest_cache/")
            if additions:
                with open(gitignore, "a") as f:
                    for a in additions:
                        f.write(f"\n{a}\n")
                self._log(f".gitignore erweitert: {additions}")
        return venv_path / "bin"

    def _apply_changes(self, wt: Path, actions: list[dict]) -> tuple[bool, str]:
        """Apply parsed actions in worktree. Returns (success, message)."""
        venv_bin = None

        for action in actions:
            if action["type"] == "file":
                file_path = wt / action["path"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(action["content"])
                self._log(f"Geschrieben: {file_path.relative_to(wt)}")
            elif action["type"] == "shell":
                cmd = action["command"]
                self._log(f"Shell: {cmd[:80]}...")

                # pip install -> in venv umleiten
                if "pip install" in cmd or "pip3 install" in cmd:
                    venv_bin = self._ensure_venv(wt)
                    if venv_bin:
                        pip_bin = str(venv_bin / "pip")
                        cmd = cmd.replace("pip3 install", f"{pip_bin} install")
                        cmd = cmd.replace("pip install", f"{pip_bin} install")
                        self._log(f"pip-Befehl in venv umgeleitet: {cmd[:80]}...")

                r = subprocess.run(
                    cmd,
                    cwd=wt,
                    capture_output=True,
                    text=True,
                    shell=True,
                    timeout=120,
                )
                if r.returncode != 0:
                    err = r.stderr[-1000:] if r.stderr else "Unknown error"
                    self._log(f"Shell fehlgeschlagen: {err}")
                    return False, f"Shell-Befehl fehlgeschlagen: {cmd[:80]}\n{err}"
        return True, "OK"

    def _run_tests(self, wt: Path) -> tuple[bool, str]:
        """Run tests in worktree. Returns (success, output)."""
        # Try pytest in venv first
        pytest_candidates = [
            wt / ".venv/bin/pytest",
            wt / "venv/bin/pytest",
        ]
        pytest_bin = None
        for candidate in pytest_candidates:
            if candidate.exists():
                pytest_bin = str(candidate)
                break

        if not pytest_bin:
            r = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True)
            if r.returncode == 0:
                pytest_bin = f"{sys.executable} -m pytest"

        if pytest_bin:
            r = subprocess.run(
                pytest_bin.split() + ["-q", "--tb=short"],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                return False, f"pytest:\n{r.stdout}\n{r.stderr}".strip()
            return True, r.stdout

        # Try npm test
        if (wt / "package.json").exists():
            r = subprocess.run(
                ["npm", "test", "--silent"],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                return False, f"npm test:\n{r.stdout}\n{r.stderr}".strip()
            return True, r.stdout

        return True, "Keine Tests gefunden"

    def _run_lint(self, wt: Path) -> tuple[bool, str]:
        """Run lint in worktree. Returns (success, output)."""
        # Try ruff in venv first
        ruff_candidates = [
            wt / ".venv/bin/ruff",
            wt / "venv/bin/ruff",
        ]
        ruff_bin = None
        for candidate in ruff_candidates:
            if candidate.exists():
                ruff_bin = str(candidate)
                break

        if not ruff_bin:
            r = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True)
            if r.returncode == 0:
                ruff_bin = f"{sys.executable} -m ruff"

        if ruff_bin:
            r = subprocess.run(
                ruff_bin.split() + ["check", "."],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                return True, r.stdout or "ruff OK"
            if r.returncode != 0 and (r.stdout or r.stderr):
                return False, f"ruff:\n{r.stdout}\n{r.stderr}".strip()

        # Try npm run lint
        if (wt / "package.json").exists():
            r = subprocess.run(
                ["npm", "run", "lint"],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                return True, r.stdout or "npm lint OK"
            if r.returncode != 0 and (r.stdout or r.stderr):
                return False, f"npm lint:\n{r.stdout}\n{r.stderr}".strip()

        return True, "Kein Linter gefunden"

    def _commit_changes(self, wt: Path, issue_number: int, title: str) -> bool:
        """Commit changes if tests pass."""
        r = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True)
        if not r.stdout.strip():
            self._log(f"Keine Aenderungen für #{issue_number}")
            return False

        test_ok, test_out = self._run_tests(wt)
        if not test_ok:
            self._log(f"Tests fehlgeschlagen fuer #{issue_number}:\n{test_out}")
            return False

        lint_ok, lint_out = self._run_lint(wt)
        if not lint_ok:
            self._log(f"Lint fehlgeschlagen fuer #{issue_number}:\n{lint_out}")
            return False

        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"Implementiere #{issue_number}: {title[:50]}"],
            cwd=wt,
            capture_output=True,
        )
        self._log(f"Commit erstellt fuer #{issue_number}")
        return True

    async def _call_ollama(self, messages: list[dict]) -> str:
        """Call Ollama API."""
        return await asyncio.to_thread(
            self.provider.chat,
            model=self.model_cfg.model,
            messages=messages,
            temperature=self.model_cfg.temperature,
            max_tokens=self.model_cfg.max_tokens,
        )

    async def process_job(self, job: Job) -> tuple[bool, Any]:
        """Process a job using Ollama."""
        self._health.state = WorkerState.BUSY
        self._write_health()

        payload = job.payload
        issue_number = payload.get("issue_number", job.issue_number)
        title = payload.get("title", "Unknown")
        body = payload.get("body", "")
        labels = payload.get("labels", [])

        if "agent-escalation-3" in labels:
            self._log(f"Issue #{issue_number} ist Eskalation-3 -> ueberspringen (ClaudeWorker zustaendig)")
            return True, {"skipped": True, "reason": "escalation-3"}

        try:
            if self._is_implementation:
                project = job.project or payload.get("project", "")
                if not project:
                    return False, {"error": "No project specified"}

                branch = f"agent/issue-{issue_number}"
                wt = self._setup_worktree(project, issue_number, branch, body)

                system_msg = f"Du bist {self.agent_name}. {self.agent_cfg.description}"
                if self.agent_cfg.prompt:
                    system_msg += f"\n\n{self.agent_cfg.prompt}"

                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": f"Issue #{issue_number}: {title}\n\n{body}"}
                ]

                content = await self._call_ollama(messages)
                self._log(f"Ollama response received ({len(content)} chars)")

                # Speichere Roh-Antwort zur Diagnose
                (wt / "OLLAMA_RESPONSE.md").write_text(content)

                # Parse und wende Aenderungen an
                actions = self._parse_code_blocks(content)
                self._log(f"Gefundene Actions: {len(actions)} ({len([a for a in actions if a['type']=='file'])} Dateien, {len([a for a in actions if a['type']=='shell'])} Shell)")

                if not actions:
                    return False, {"error": "Keine Code-Bloecke in Ollama-Antwort gefunden", "content": content[:500]}

                ok, msg = self._apply_changes(wt, actions)
                if not ok:
                    return False, {"error": msg, "content": content[:500]}

                # Commit am Ende wenn Tests gruen
                committed = self._commit_changes(wt, issue_number, title)

                return True, {
                    "committed": committed,
                    "files_written": len([a for a in actions if a["type"] == "file"]),
                    "agent": self.agent_name,
                }
            else:
                system_msg = f"Du bist {self.agent_name}. {self.agent_cfg.description}"
                if self.agent_cfg.prompt:
                    system_msg += f"\n\n{self.agent_cfg.prompt}"

                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": f"Issue #{issue_number}: {title}\n\n{body}"}
                ]

                content = await self._call_ollama(messages)
                return True, {"content": content, "agent": self.agent_name}

        except Exception as e:
            self._log(f"Fehler bei #{issue_number}: {e}")
            return False, {"error": str(e), "agent": self.agent_name}

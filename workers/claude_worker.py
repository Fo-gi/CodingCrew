"""Claude CLI Worker für Cloud-Modell-Ausführung."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Optional

from jobqueue import Job
from src.models import CrewConfig
from .base import BaseWorker, WorkerState


class ClaudeWorker(BaseWorker):
    """
    Worker der Claude CLI (claude -p) für komplexe Tasks nutzt.

    WICHTIG: Dieser Worker wird NUR für agent-escalation-3 Jobs verwendet.
    Das ist das automatische Fallback nach 3 fehlgeschlagenen Ollama-Versuchen.
    """

    def __init__(
        self,
        agent_name: str = "claude_fallback",
        config: CrewConfig = None,
        workspace_base: Optional[Path] = None,
        worktree_base: Optional[Path] = None,
        queue_dir: Optional[Path] = None,
        health_dir: Optional[Path] = None,
    ):
        super().__init__(
            worker_type=f"claude-{agent_name}",
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

    def _get_next_job(self) -> Optional[Job]:
        """
        Get next job from queue - NUR agent-escalation-3 Jobs!

        Dieser Worker verarbeitet ausschliesslich Eskalation Stufe 3.
        """
        # Hole Job aus der Queue, aber nur wenn es ein escalation-3 Job ist
        job = self.queue.dequeue(worker_id=self.worker_id, job_type=None)

        if job:
            # Prüfen ob es ein Eskalation-3 Job ist
            labels = job.payload.get("labels", [])
            if "agent-escalation-3" not in labels:
                # Kein Eskalation-3 Job - zurück in die Queue
                self._log(f"Überspringe Job {job.id}: nicht agent-escalation-3")
                # Job wieder als pending markieren (einfach: zurück in pending)
                self.queue.fail(job, "Not an escalation-3 job", retry=False)
                return None

        return job

    def _log(self, msg: str):
        """Log message to worker log file."""
        from datetime import datetime
        log_file = Path.home() / "CodingCrew" / "logs" / f"{self.worker_type}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat()
        with open(log_file, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def _build_prompt(self, payload: dict, attempt: int = 1) -> str:
        """Build prompt for claude -p."""
        base = self.agent_cfg.prompt + "\n\n"
        base += f"Hard cap: {self.config.limits.max_iterations} iterations, ${self.config.limits.task_budget_usd}.\n"

        if attempt > 1:
            base += f"\nWICHTIG (Versuch {attempt}/{self.config.limits.max_iterations}): "
            base += "Ein vorheriger Versuch ist fehlgeschlagen. Lies ESCALATION.md falls vorhanden.\n"

        return base

    def _setup_worktree(self, project: str, issue_number: int, branch: str, body: str) -> Path:
        """Create or reuse worktree for issue."""
        wt = self.worktree_base / f"{project}/issue-{issue_number}"

        workspace = self.workspace_base / project

        # Ensure workspace exists
        if not workspace.exists():
            subprocess.run(
                ["gh", "repo", "clone", project, str(workspace)],
                check=True,
                capture_output=True,
            )

        # Check if branch exists
        r = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", branch],
            capture_output=True,
        )

        if r.returncode == 0 and wt.exists():
            # Reuse existing worktree (escalation case)
            subprocess.run(
                ["git", "-C", str(workspace), "worktree", "add", str(wt), branch],
                check=True,
                capture_output=True,
            )
        else:
            # Create new worktree
            if wt.exists():
                subprocess.run(["rm", "-rf", str(wt)], check=True)
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

        # Setup git config
        subprocess.run(["git", "config", "user.email", "agent@localhost"], cwd=wt, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Claude Agent"], cwd=wt, check=True, capture_output=True)

        # Write SPEC.md
        (wt / "SPEC.md").write_text(body)

        # Create agent state dir
        agent_dir = wt / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / "iter").write_text("0")

        return wt

    async def _run_claude(self, wt: Path, prompt: str, timeout_minutes: int = 240) -> tuple[int, Path]:
        """Run claude -p and return exit code + log path."""
        log_dir = Path.home() / "CodingCrew" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        log_file = log_dir / f"claude-{self.agent_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

        proc = await asyncio.create_subprocess_exec(
            "timeout", str(timeout_minutes * 60),
            "claude", "-p", prompt,
            "--model", "claude-sonnet-4-6",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "acceptEdits",
            "--max-turns", "200",
            cwd=str(wt),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stdout, _ = await proc.communicate()
        log_file.write_bytes(stdout)

        return proc.returncode or 0, log_file

    def _check_success(self, wt: Path) -> tuple[bool, str]:
        """Check if work was successful (tests pass, has diff)."""
        # Check for diff
        r = subprocess.run(
            ["git", "diff", "--quiet", "origin/main"],
            cwd=wt,
            capture_output=True,
        )
        if r.returncode == 0:
            return False, "Kein Diff vs origin/main"

        # Check tests if pytest config exists
        if (wt / "pyproject.toml").exists() or (wt / "pytest.ini").exists() or (wt / "tests").is_dir():
            pytest_bin = None
            for candidate in [wt / ".venv/bin/pytest", wt / "venv/bin/pytest"]:
                if candidate.exists():
                    pytest_bin = str(candidate)
                    break

            if not pytest_bin:
                import sys
                r = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True)
                if r.returncode == 0:
                    pytest_bin = f"{sys.executable} -m pytest"

            if pytest_bin:
                r = subprocess.run(
                    pytest_bin.split() + ["-q", "--tb=line"],
                    cwd=wt,
                    capture_output=True,
                    text=True,
                )
                if r.returncode != 0:
                    return False, f"pytest failures:\n{r.stdout[-2000:]}"

        # Check npm test if package.json exists
        if (wt / "package.json").exists() and (wt / "node_modules").is_dir():
            r = subprocess.run(
                ["npm", "test", "--silent"],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                return False, f"npm test failures:\n{r.stdout[-2000:]}"

        return True, "OK"

    async def process_job(self, job: Job) -> tuple[bool, Any]:
        """Process a job using Claude CLI."""
        self._health.state = WorkerState.BUSY
        self._write_health()

        try:
            payload = job.payload
            project = job.project or payload.get("project", "")
            issue_number = payload.get("issue_number", job.issue_number)
            title = payload.get("title", "Unknown")
            body = payload.get("body", "")
            attempt = payload.get("attempt", 1)

            if not project:
                return False, {"error": "No project specified in job"}

            branch = f"agent/issue-{issue_number}"
            wt = self._setup_worktree(project, issue_number, branch, body)

            prompt = self._build_prompt(payload, attempt)
            exit_code, log_file = await self._run_claude(wt, prompt)

            # Auto-commit uncommitted changes
            r = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True)
            if r.stdout.strip():
                subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", f"agent: Implementiere #{issue_number} - {title[:50]}"],
                    cwd=wt,
                    capture_output=True,
                )

            success, message = self._check_success(wt)

            # Cleanup worktree
            subprocess.run(
                ["git", "-C", str(self.workspace_base / project), "worktree", "remove", "--force", str(wt)],
                capture_output=True,
            )

            return success, {
                "exit_code": exit_code,
                "log_file": str(log_file),
                "message": message,
                "agent": self.agent_name,
            }

        except Exception as e:
            return False, {"error": str(e), "agent": self.agent_name}

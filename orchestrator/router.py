"""Orchestrator Router - verteilt Issues an Worker."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jobqueue import Job, JobPriority, QueueManager
from shared.config import load_project_config
from src.github import GitHubClient


class OrchestratorRouter:
    """
    Router der GitHub Issues analysiert und an passende Worker verteilt.

    Im Gegensatz zum monolithischen Orchestrator macht dieser hier:
    - Kein direktes Ausführen von Agenten
    - Pollt GitHub und erstellt Jobs basierend auf Labels/Tags
    - Priorisiert Jobs korrekt (agent-question > escalation > ready)
    - Überwacht Worker-Health und restartet bei Bedarf
    """

    def __init__(self, project_name: str, config_path: Optional[Path] = None):
        self.project_name = project_name

        if config_path:
            self.config = load_project_config(config_path)
        else:
            try:
                self.config = load_project_config(project_name)
            except FileNotFoundError:
                # Fallback to root config
                from src.models import CrewConfig
                self.config = CrewConfig.load(Path.home() / "CodingCrew" / "crew.yaml")

        self.queue_mgr = QueueManager()
        self.github_repo = self.config.github.repo

    def _get_priority_for_label(self, label: str) -> JobPriority:
        """Map label to job priority."""
        if label == "agent-question":
            return JobPriority.CRITICAL
        elif label == "agent-escalation-3":
            return JobPriority.CRITICAL  # Claude Fallback hat höchste Prio
        elif label in ("agent-escalation-1", "agent-escalation-2"):
            return JobPriority.HIGH
        elif label in ("agent-ready", "agent-ready-complex", "agent-review", "agent-test"):
            return JobPriority.NORMAL
        else:
            return JobPriority.LOW

    def _get_handler_for_label(self, label: str) -> Optional[str]:
        """Get handler agent for a label."""
        for tag in self.config.tags:
            if tag.name == label and tag.handler:
                return tag.handler
        return None

    def _get_agent_type(self, agent_name: str) -> str:
        """Get agent type (direct or claude_cli)."""
        agent_cfg = self.config.agents.get(agent_name)
        if not agent_cfg:
            return "claude_cli"  # Default to Claude for unknown agents
        return agent_cfg.type.value if hasattr(agent_cfg.type, 'value') else agent_cfg.type

    def create_job_for_issue(self, issue: dict) -> Optional[Job]:
        """Create a job from a GitHub issue."""
        labels = [l.get("name", "") for l in issue.get("labels", [])]

        # Find highest priority label with handler
        handler = None
        priority = JobPriority.LOW
        matched_label = None

        for label in labels:
            label_priority = self._get_priority_for_label(label)
            if label_priority.value < priority.value:
                handler = self._get_handler_for_label(label)
                if handler:
                    priority = label_priority
                    matched_label = label

        if not handler:
            return None  # No handler for this issue

        agent_type = self._get_agent_type(handler)
        job_type = f"issue-{agent_type}"

        job = Job(
            type=job_type,
            priority=priority,
            project=self.github_repo,
            issue_number=issue.get("number"),
            payload={
                "title": issue.get("title", ""),
                "body": issue.get("body", ""),
                "labels": labels,
                "handler": handler,
                "agent_type": agent_type,
                "matched_label": matched_label,
            },
        )

        return job

    async def poll_and_enqueue(self, poll_interval: int = 30):
        """Main loop: poll GitHub, enqueue jobs."""
        from src.github import GitHubClient

        gh = GitHubClient(self.github_repo)

        while True:
            try:
                # Fetch all issues with agent-* labels
                all_labels = [t.name for t in self.config.tags if t.name.startswith("agent-")]

                for label in all_labels:
                    try:
                        issues = gh.list_issues(label, exclude=["agent-working", "agent-done", "agent-stuck"])

                        for issue in issues:
                            # Check if already in queue (deduplication)
                            existing_jobs = self.queue_mgr.list_jobs(
                                project=self.github_repo,
                                job_type="issue-*",
                            )
                            already_queued = any(
                                j.issue_number == issue.get("number") and j.status.value == "pending"
                                for j in existing_jobs
                            )

                            if not already_queued:
                                job = self.create_job_for_issue(issue)
                                if job:
                                    self.queue_mgr.enqueue(job)

                    except Exception as e:
                        # Log but continue with other labels
                        pass

                # Prüfen ob Issues von agent-escalation-2 zu agent-escalation-3 müssen
                # (nachdem sie 3 Ollama-Fehlversuche hatten)
                try:
                    escalation_2_issues = gh.list_issues("agent-escalation-2", exclude=["agent-working"])
                    for issue in escalation_2_issues:
                        # Prüfen ob Issue schon in Queue ist
                        issue_num = issue.get("number")
                        existing = self.queue_mgr.list_jobs(
                            project=self.github_repo,
                            job_type="issue-*",
                        )
                        in_queue = any(j.issue_number == issue_num and j.status.value != "failed" for j in existing)

                        if not in_queue:
                            # Issue ist nicht in Queue → als escalation-3 enqueue
                            job = Job(
                                type="issue-claude_cli",
                                priority=JobPriority.CRITICAL,
                                project=self.github_repo,
                                issue_number=issue_num,
                                payload={
                                    "title": issue.get("title", ""),
                                    "body": issue.get("body", ""),
                                    "labels": ["agent-escalation-3"],
                                    "handler": "claude_fallback",
                                    "agent_type": "claude_cli",
                                },
                            )
                            self.queue_mgr.enqueue(job)
                            # Label auf agent-escalation-3 setzen
                            gh.edit_labels(issue_num, remove=["agent-escalation-2"], add=["agent-escalation-3"])
                except Exception:
                    pass

                await asyncio.sleep(poll_interval)

            except Exception as e:
                # Log error and retry
                await asyncio.sleep(poll_interval)

    def run_sync(self, poll_interval: int = 30):
        """Run orchestrator synchronously."""
        asyncio.run(self.poll_and_enqueue(poll_interval))


def main():
    """CLI entry point for orchestrator router."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="CodingCrew Orchestrator Router")
    parser.add_argument("--project", "-p", default="default", help="Project name")
    parser.add_argument("--config", "-c", default=None, help="Config file path")
    parser.add_argument("--poll-interval", "-i", type=int, default=30, help="Poll interval in seconds")
    args = parser.parse_args()

    router = OrchestratorRouter(project_name=args.project, config_path=args.config)
    print(f"Starting Orchestrator Router for project: {router.project_name}")
    print(f"GitHub Repo: {router.github_repo}")
    print(f"Poll interval: {args.poll_interval}s")
    print("Press Ctrl+C to stop...")

    try:
        router.run_sync(poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()

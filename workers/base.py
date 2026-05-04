"""Base Worker Klasse für alle CodingCrew Worker."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from jobqueue import Job, JobStatus, QueueManager


class WorkerState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"
    DEAD = "dead"


@dataclass
class WorkerHealth:
    """Health status of a worker."""
    worker_id: str
    worker_type: str
    state: WorkerState = WorkerState.IDLE
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_job_id: Optional[str] = None
    jobs_completed: int = 0
    jobs_failed: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "worker_type": self.worker_type,
            "state": self.state.value,
            "last_heartbeat": self.last_heartbeat,
            "current_job_id": self.current_job_id,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "started_at": self.started_at,
            "error_message": self.error_message,
        }


class BaseWorker(ABC):
    """Abstract base class for all workers."""

    def __init__(
        self,
        worker_type: str,
        queue_dir: Optional[Path] = None,
        health_dir: Optional[Path] = None,
    ):
        self.worker_id = f"{worker_type}-{uuid.uuid4().hex[:8]}"
        self.worker_type = worker_type
        self.queue = QueueManager(queue_dir)
        self.health_dir = health_dir or (Path.home() / "CodingCrew" / "health")
        self.health_dir.mkdir(parents=True, exist_ok=True)
        self.health_file = self.health_dir / f"{self.worker_id}.json"

        self._state = WorkerState.IDLE
        self._health = WorkerHealth(worker_id=self.worker_id, worker_type=worker_type)
        self._stop_requested = False
        self._current_job: Optional[Job] = None

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals."""
        self._stop_requested = True
        self._state = WorkerState.STOPPING
        self._health.state = WorkerState.STOPPING
        self._write_health()

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def current_job(self) -> Optional[Job]:
        return self._current_job

    def _log(self, msg: str):
        """Log message to worker log file."""
        from datetime import datetime
        log_file = Path.home() / "CodingCrew" / "logs" / f"{self.worker_type}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat()
        with open(log_file, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def _write_health(self):
        """Write health status to file."""
        self._health.last_heartbeat = datetime.now(timezone.utc).isoformat()
        self.health_file.write_text(json.dumps(self._health.to_dict(), indent=2))

    def _read_health(self, worker_id: str) -> Optional[WorkerHealth]:
        """Read health status from file."""
        health_file = self.health_dir / f"{worker_id}.json"
        if not health_file.exists():
            return None
        try:
            data = json.loads(health_file.read_text())
            return WorkerHealth(
                worker_id=data["worker_id"],
                worker_type=data["worker_type"],
                state=WorkerState(data["state"]),
                last_heartbeat=data["last_heartbeat"],
                current_job_id=data.get("current_job_id"),
                jobs_completed=data.get("jobs_completed", 0),
                jobs_failed=data.get("jobs_failed", 0),
                started_at=data.get("started_at"),
                error_message=data.get("error_message"),
            )
        except Exception:
            return None

    @abstractmethod
    async def process_job(self, job: Job) -> tuple[bool, Any]:
        """
        Process a single job.

        Returns:
            tuple[bool, Any]: (success, result)
        """
        pass

    def _get_next_job(self) -> Optional[Job]:
        """Get next job from queue."""
        return self.queue.dequeue(worker_id=self.worker_id, job_type=None)

    async def run(self, poll_interval: float = 5.0):
        """Main worker loop."""
        self._write_health()

        while not self._stop_requested:
            # Check for next job
            job = self._get_next_job()

            if job:
                self._state = WorkerState.BUSY
                self._current_job = job
                self._health.state = WorkerState.BUSY
                self._health.current_job_id = job.id
                self._write_health()

                try:
                    success, result = await self.process_job(job)

                    if success:
                        self.queue.complete(job, result)
                        self._health.jobs_completed += 1
                    else:
                        will_retry = self.queue.fail(job, str(result), retry=True)
                        if not will_retry:
                            self._health.jobs_failed += 1
                except Exception as e:
                    will_retry = self.queue.fail(job, str(e), retry=True)
                    if not will_retry:
                        self._health.jobs_failed += 1
                        self._health.error_message = str(e)
                finally:
                    self._current_job = None
                    self._health.current_job_id = None
                    self._state = WorkerState.IDLE
                    self._health.state = WorkerState.IDLE
                    self._write_health()
            else:
                # No jobs, wait before polling again
                await asyncio.sleep(poll_interval)
                self._write_health()

        # Graceful shutdown
        self._write_health()

    def run_sync(self, poll_interval: float = 5.0):
        """Run worker synchronously (wrapper for asyncio.run)."""
        asyncio.run(self.run(poll_interval))

    @classmethod
    def list_all_workers(cls, health_dir: Optional[Path] = None) -> list[WorkerHealth]:
        """List all known workers and their health status."""
        health_dir = health_dir or (Path.home() / "CodingCrew" / "health")
        if not health_dir.exists():
            return []

        workers = []
        for health_file in health_dir.glob("*.json"):
            try:
                data = json.loads(health_file.read_text())
                worker = WorkerHealth(
                    worker_id=data["worker_id"],
                    worker_type=data["worker_type"],
                    state=WorkerState(data["state"]),
                    last_heartbeat=data["last_heartbeat"],
                    current_job_id=data.get("current_job_id"),
                    jobs_completed=data.get("jobs_completed", 0),
                    jobs_failed=data.get("jobs_failed", 0),
                    started_at=data.get("started_at"),
                    error_message=data.get("error_message"),
                )
                workers.append(worker)
            except Exception:
                continue

        return workers

    @classmethod
    def cleanup_stale_workers(
        cls,
        health_dir: Optional[Path] = None,
        stale_threshold_seconds: int = 300,
    ) -> list[str]:
        """Remove health files for workers that haven't sent heartbeat."""
        health_dir = health_dir or (Path.home() / "CodingCrew" / "health")
        if not health_dir.exists():
            return []

        cutoff = datetime.now(timezone.utc).timestamp() - stale_threshold_seconds
        removed = []

        for health_file in health_dir.glob("*.json"):
            try:
                data = json.loads(health_file.read_text())
                last_heartbeat = datetime.fromisoformat(data["last_heartbeat"]).timestamp()
                if last_heartbeat < cutoff:
                    health_file.unlink()
                    removed.append(data["worker_id"])
            except Exception:
                continue

        return removed

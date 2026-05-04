"""Queue Management Routes."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from jobqueue import Job, JobPriority, JobStatus, QueueManager

router = APIRouter()


@router.get("/stats")
async def get_queue_stats():
    """Get queue statistics."""
    queue_mgr = QueueManager()
    stats = queue_mgr.get_stats()
    return stats


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    project: Optional[str] = None,
    job_type: Optional[str] = None,
):
    """List jobs with optional filters."""
    queue_mgr = QueueManager()

    try:
        job_status = JobStatus(status) if status else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    jobs = queue_mgr.list_jobs(status=job_status, project=project, job_type=job_type)
    return {
        "jobs": [j.to_dict() for j in jobs],
        "count": len(jobs),
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Get specific job by ID."""
    queue_mgr = QueueManager()
    job = queue_mgr.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job.to_dict()


@router.post("/jobs")
async def enqueue_job(job_data: dict[str, Any]):
    """Add new job to queue."""
    queue_mgr = QueueManager()

    # Extract fields with defaults
    job_type = job_data.get("type", "issue")
    project = job_data.get("project", "")
    issue_number = job_data.get("issue_number")
    payload = job_data.get("payload", {})

    priority_str = job_data.get("priority", "normal")
    priority_map = {
        "critical": JobPriority.CRITICAL,
        "high": JobPriority.HIGH,
        "normal": JobPriority.NORMAL,
        "low": JobPriority.LOW,
    }
    priority = priority_map.get(priority_str.lower(), JobPriority.NORMAL)

    job = Job(
        type=job_type,
        priority=priority,
        project=project,
        issue_number=issue_number,
        payload=payload,
    )

    job_id = queue_mgr.enqueue(job)
    return {"job_id": job_id, "status": "queued"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a pending job."""
    queue_mgr = QueueManager()
    job = queue_mgr.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.PENDING:
        raise HTTPException(status_code=400, detail="Can only cancel pending jobs")

    # Remove from pending
    pending_file = queue_mgr.queue_dir / "pending" / f"{job.priority.value}_{job_id}.json"
    if pending_file.exists():
        pending_file.unlink()
        return {"status": "cancelled", "job_id": job_id}

    raise HTTPException(status_code=404, detail="Job file not found")


@router.post("/purge")
async def purge_queue(status: Optional[str] = None):
    """Purge jobs from queue."""
    queue_mgr = QueueManager()

    try:
        job_status = JobStatus(status) if status else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    queue_mgr.purge(state=job_status)
    return {"status": "purged", "filter": status}

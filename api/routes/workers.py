"""Worker Management Routes."""
from __future__ import annotations

from fastapi import APIRouter

from workers.base import BaseWorker

router = APIRouter()


@router.get("")
async def list_workers():
    """List all workers and their health status."""
    workers = BaseWorker.list_all_workers()
    return {
        "workers": [w.to_dict() for w in workers],
        "count": len(workers),
    }


@router.get("/{worker_id}")
async def get_worker(worker_id: str):
    """Get specific worker health status."""
    workers = BaseWorker.list_all_workers()
    for w in workers:
        if w.worker_id == worker_id:
            return w.to_dict()
    return {"error": "Worker not found"}, 404


@router.post("/cleanup")
async def cleanup_stale_workers(threshold_seconds: int = 300):
    """Remove stale worker health files."""
    removed = BaseWorker.cleanup_stale_workers(stale_threshold_seconds=threshold_seconds)
    return {"removed_workers": removed, "count": len(removed)}

"""Project Management Routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from shared.config import list_projects, load_project_config, save_project_config

router = APIRouter()


@router.get("")
async def list_projects_endpoint():
    """List all available projects."""
    projects = list_projects()
    return {"projects": projects, "count": len(projects)}


@router.get("/{project_name}")
async def get_project(project_name: str):
    """Get project configuration."""
    try:
        config = load_project_config(project_name)
        return {
            "name": project_name,
            "github_repo": config.github.repo,
            "agents": list(config.agents.keys()),
            "models": list(config.models.keys()),
            "tags": [{"name": t.name, "priority": t.priority, "handler": t.handler} for t in config.tags],
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")


@router.post("/{project_name}/config")
async def update_project_config(project_name: str, config: dict[str, Any]):
    """Update project configuration."""
    config_path = save_project_config(project_name, config)
    return {"path": str(config_path), "status": "saved"}


@router.get("/{project_name}/status")
async def get_project_status(project_name: str):
    """Get current project status (queue, active workers, recent jobs)."""
    from jobqueue import QueueManager

    try:
        config = load_project_config(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    queue_mgr = QueueManager()
    stats = queue_mgr.get_stats()

    # Filter jobs by project
    all_jobs = queue_mgr.list_jobs()
    project_jobs = [j for j in all_jobs if j.project == project_name]

    pending = len([j for j in project_jobs if j.status.value == "pending"])
    processing = len([j for j in project_jobs if j.status.value == "processing"])

    return {
        "project": project_name,
        "repo": config.github.repo,
        "queue": {
            "pending": pending,
            "processing": processing,
        },
        "total_queue_stats": stats,
    }

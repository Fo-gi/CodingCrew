"""Shared utilities and base classes for CodingCrew microservices."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    """Current UTC timestamp."""
    return datetime.now(timezone.utc)


def generate_job_id() -> str:
    """Generate unique job ID for queue."""
    return uuid.uuid4().hex[:12]


def generate_issue_key(issue_number: int, project_slug: str) -> str:
    """Generate unique key for deduplication."""
    content = f"{project_slug}:{issue_number}:{utc_now().isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def json_dumps_safe(obj: Any) -> str:
    """Safe JSON serialization with datetime handling."""
    def default_serializer(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        return str(o)
    return json.dumps(obj, default=default_serializer, indent=2)


def json_loads_safe(content: str) -> Any:
    """Safe JSON deserialization."""
    return json.loads(content)

"""GitHub Webhook Routes."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from jobqueue import Job, JobPriority, QueueManager

router = APIRouter()


def _verify_signature(payload: bytes, signature: str | None) -> bool:
    """Verify GitHub webhook signature."""
    if not signature:
        return False

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        # No secret configured, skip verification (not recommended for production)
        return True

    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
):
    """
    Handle GitHub webhook events.

    Events:
    - issues.opened: New issue created
    - issues.labeled: Issue label changed
    - issue_comment.created: New comment on issue
    """
    body = await request.body()

    # Verify signature if secret is configured
    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    queue_mgr = QueueManager()

    if x_github_event == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        repo = payload.get("repository", {}).get("full_name", "")

        if action == "opened":
            # New issue - enqueue for processing
            labels = [l.get("name", "") for l in issue.get("labels", [])]

            # Determine priority based on labels
            priority = JobPriority.NORMAL
            if "agent-question" in labels:
                priority = JobPriority.CRITICAL
            elif any("escalation" in l for l in labels):
                priority = JobPriority.HIGH

            job = Job(
                type="issue",
                priority=priority,
                project=repo,
                issue_number=issue.get("number"),
                payload={
                    "title": issue.get("title", ""),
                    "body": issue.get("body", ""),
                    "labels": labels,
                    "author": issue.get("user", {}).get("login", ""),
                },
            )
            queue_mgr.enqueue(job)

            return {"status": "queued", "job_id": job.id, "issue": issue.get("number")}

        elif action == "labeled":
            # Label changed - may need to re-queue or prioritize
            label = payload.get("label", {}).get("name", "")

            if label == "agent-question":
                # Human needs to answer - high priority
                job = Job(
                    type="question",
                    priority=JobPriority.CRITICAL,
                    project=repo,
                    issue_number=issue.get("number"),
                    payload={"label": label, "action": "waiting_for_answer"},
                )
                queue_mgr.enqueue(job)
                return {"status": "queued", "job_id": job.id, "type": "question"}

    elif x_github_event == "issue_comment":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        comment = payload.get("comment", {})
        repo = payload.get("repository", {}).get("full_name", "")

        if action == "created":
            # Check if comment is from human (not bot)
            author = comment.get("user", {}).get("login", "")
            is_bot = "bot" in author.lower() or author in ["github-actions[bot]", "Claude Agent"]

            if not is_bot and issue.get("state") == "open":
                # Human commented - check if issue was waiting for answer
                labels = [l.get("name", "") for l in issue.get("labels", [])]

                if "agent-question" in labels:
                    # Move back to queue with high priority
                    job = Job(
                        type="issue",
                        priority=JobPriority.CRITICAL,
                        project=repo,
                        issue_number=issue.get("number"),
                        payload={
                            "title": issue.get("title", ""),
                            "body": issue.get("body", ""),
                            "labels": labels,
                            "new_comment": comment.get("body", ""),
                            "commenter": author,
                        },
                    )
                    queue_mgr.enqueue(job)
                    return {"status": "queued", "job_id": job.id, "type": "issue_updated"}

    return {"status": "ignored", "event": x_github_event, "action": action}


@router.get("/github/configure")
async def configure_github_webhook():
    """
    Instructions for configuring GitHub webhook.

    In production, this would create/update the webhook via GitHub API.
    """
    # This would need GITHUB_TOKEN to actually create the webhook
    gh_token = os.environ.get("GITHUB_TOKEN", "")

    if not gh_token:
        return {
            "error": "GITHUB_TOKEN not configured",
            "instructions": [
                "1. Set GITHUB_TOKEN environment variable",
                "2. Go to GitHub repo Settings > Webhooks",
                "3. Add webhook with URL: https://your-server.com/api/v1/webhooks/github",
                "4. Content type: application/json",
                "5. Secret: Set GITHUB_WEBHOOK_SECRET environment variable",
                "6. Events: Issues, Issue comments",
            ],
        }

    # In production: use gh CLI or GitHub API to create webhook
    return {
        "status": "configured",
        "webhook_url": "https://your-server.com/api/v1/webhooks/github",
        "events": ["issues", "issue_comment"],
    }

#!/usr/bin/env bash
# Startet CodingCrew Orchestrator Router
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# Parse arguments
PROJECT="${1:-default}"
POLL_INTERVAL="${2:-30}"

# Load environment
if [ -f "$REPO_DIR/.env" ]; then
    set -a && source "$REPO_DIR/.env" && set +a
fi

echo "Starting Orchestrator Router..."
echo "  Project: $PROJECT"
echo "  Poll interval: ${POLL_INTERVAL}s"
echo "  Queue: ~/CodingCrew/queue/"
echo "Press Ctrl+C to stop..."

"$VENV/bin/python3" -m orchestrator.router --project "$PROJECT" --poll-interval "$POLL_INTERVAL"

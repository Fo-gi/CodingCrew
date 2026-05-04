#!/usr/bin/env bash
# Startet CodingCrew API Gateway
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# Check if API dependencies are installed
if ! "$VENV/bin/pip" show fastapi >/dev/null 2>&1; then
    echo "Installing API dependencies..."
    "$VENV/bin/pip" install -e ".[api]" --quiet
fi

# Load environment
if [ -f "$REPO_DIR/.env" ]; then
    set -a && source "$REPO_DIR/.env" && set +a
fi

# Start API
echo "Starting CodingCrew API Gateway..."
echo "  Docs: http://localhost:8000/docs"
echo "  Health: http://localhost:8000/health"
"$VENV/bin/uvicorn" api.app:create_app --factory --host 0.0.0.0 --port 8000 --reload

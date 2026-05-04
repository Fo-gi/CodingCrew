#!/usr/bin/env bash
# Startet einen CodingCrew Worker
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# Parse arguments
WORKER_TYPE="${1:-ollama}"
AGENT_NAME="${2:-junior_dev}"

if [ "$WORKER_TYPE" != "ollama" ] && [ "$WORKER_TYPE" != "claude" ]; then
    echo "Usage: $0 <ollama|claude> [agent_name]"
    echo "  ollama: Run Ollama-based worker (local models)"
    echo "  claude: Run Claude CLI-based worker (cloud models)"
    exit 1
fi

# Load environment
if [ -f "$REPO_DIR/.env" ]; then
    set -a && source "$REPO_DIR/.env" && set +a
fi

echo "Starting $WORKER_TYPE worker for agent: $AGENT_NAME"
echo "  Queue: ~/CodingCrew/queue/"
echo "  Health: ~/CodingCrew/health/"
echo "Press Ctrl+C to stop..."

if [ "$WORKER_TYPE" == "ollama" ]; then
    "$VENV/bin/python3" -c "
import asyncio
from workers.ollama_worker import OllamaWorker
from src.models import CrewConfig
from pathlib import Path

config = CrewConfig.load(Path.home() / 'CodingCrew' / 'crew.yaml')
worker = OllamaWorker('$AGENT_NAME', config)
asyncio.run(worker.run())
"
else
    "$VENV/bin/python3" -c "
import asyncio
from workers.claude_worker import ClaudeWorker
from src.models import CrewConfig
from pathlib import Path

config = CrewConfig.load(Path.home() / 'CodingCrew' / 'crew.yaml')
worker = ClaudeWorker('$AGENT_NAME', config)
asyncio.run(worker.run())
"
fi

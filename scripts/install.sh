#!/usr/bin/env bash
set -euo pipefail

echo "=== CodingCrew Installation ==="

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# 1. Runtime-Verzeichnisse anlegen
echo "[1/6] Erstelle Verzeichnisse..."
mkdir -p "$REPO_DIR/logs" "$REPO_DIR/worktrees" "$REPO_DIR/workspace" "$REPO_DIR/jobqueue" "$REPO_DIR/health"

# 2. Virtualenv + Dependencies
echo "[2/6] Installiere Python-Dependencies..."
cd "$REPO_DIR"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
# Erst requirements.txt versuchen, dann pyproject.toml als Fallback
if [ -f "$REPO_DIR/requirements.txt" ]; then
    "$VENV/bin/pip" install -r requirements.txt --quiet
else
    "$VENV/bin/pip" install -e ".[api]" --quiet 2>/dev/null || "$VENV/bin/pip" install fastapi uvicorn pydantic pyyaml requests --quiet
fi

# 3. .env anlegen wenn nicht vorhanden
if [ ! -f "$REPO_DIR/.env" ]; then
    echo ""
    echo "[WARN] .env fehlt. Vorlage wird kopiert..."
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    chmod 600 "$REPO_DIR/.env"
    echo "  => Bitte ausfuellen: nano $REPO_DIR/.env"
    echo "  => Danach: bash $REPO_DIR/scripts/install.sh"
    exit 0
fi

# 4. GitHub-Setup (Repo + Labels anlegen)
echo "[3/6] GitHub-Setup..."
set -a && source "$REPO_DIR/.env" && set +a
"$VENV/bin/python3" "$REPO_DIR/scripts/setup_github.py" --config "$REPO_DIR/crew.yaml" 2>&1 \
    || echo "  [WARN] GitHub-Setup fehlgeschlagen — gh auth login pruefen"

# 5. Hooks ausfuehrbar machen
echo "[4/6] Hook-Rechte setzen..."
chmod +x "$REPO_DIR/src/hooks/"*.py

# 6. systemd Services installieren
echo "[5/6] systemd Services installieren..."
bash "$REPO_DIR/scripts/install-systemd.sh"

# 7. API testen
echo "[6/6] Teste API-Import..."
"$VENV/bin/python3" -c "from api.app import create_app; print('  → API import OK')"

echo ""
echo "=== Fertig ==="
echo ""
echo "Services starten:"
echo "  systemctl --user start api-gateway.service"
echo "  systemctl --user start orchestrator-router.service"
echo "  systemctl --user start worker-ollama.service"
echo "  systemctl --user start worker-claude.service"
echo ""
echo "Oder manuell ohne systemd:"
echo "  bash $REPO_DIR/scripts/run-api.sh"
echo "  bash $REPO_DIR/scripts/run-orchestrator.sh"
echo "  bash $REPO_DIR/scripts/run-worker.sh ollama junior_dev"
echo "  bash $REPO_DIR/scripts/run-worker.sh claude senior_dev"
echo ""
echo "Logs:   tail -f $REPO_DIR/logs/*.log"
echo "API:    http://localhost:8000/docs"
echo "Config: nano $REPO_DIR/crew.yaml"

#!/usr/bin/env bash
set -euo pipefail

echo "=== CodingCrew Installation ==="

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# 1. Runtime-Verzeichnisse anlegen
echo "[1/5] Erstelle Verzeichnisse..."
mkdir -p "$REPO_DIR/logs" "$REPO_DIR/worktrees" "$REPO_DIR/workspace"

# 2. Virtualenv + Dependencies
echo "[2/5] Installiere Python-Dependencies..."
cd "$REPO_DIR"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -e . --quiet

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
echo "[3/5] GitHub-Setup..."
set -a && source "$REPO_DIR/.env" && set +a
"$VENV/bin/python3" "$REPO_DIR/scripts/setup_github.py" --config "$REPO_DIR/crew.yaml" 2>&1 \
    || echo "  [WARN] GitHub-Setup fehlgeschlagen — gh auth login pruefen"

# 5. Hooks ausfuehrbar machen
echo "[4/5] Hook-Rechte setzen..."
chmod +x "$REPO_DIR/src/hooks/"*.py

# 6. systemd-Service
echo "[5/5] systemd-Service installieren & starten..."
mkdir -p "$HOME/.config/systemd/user"
cp "$REPO_DIR/systemd/orchestrator.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable orchestrator.service
systemctl --user restart orchestrator.service

echo ""
echo "=== Fertig ==="
systemctl --user status orchestrator --no-pager -l || true
echo ""
echo "Logs:   tail -f $REPO_DIR/logs/orchestrator.log"
echo "Config: nano $REPO_DIR/crew.yaml"
echo "Stop:   systemctl --user stop orchestrator"

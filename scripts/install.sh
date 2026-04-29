#!/usr/bin/env bash
set -euo pipefail

echo "=== CodingCrew Installation ==="

REPO_DIR="$HOME/CodingCrew"
mkdir -p "$REPO_DIR/logs"

# 1. Python-Dependencies
echo "[1/6] Installiere Python-Dependencies..."
cd "$REPO_DIR"
if command -v pip3 &>/dev/null; then
    pip3 install -e . --quiet || pip3 install pydantic pyyaml requests --quiet
elif command -v pip &>/dev/null; then
    pip install -e . --quiet || pip install pydantic pyyaml requests --quiet
fi

# 2. .env prüfen
if [ ! -f "$REPO_DIR/.env" ]; then
    echo "[WARN] .env nicht gefunden. Bitte erstellen:"
    echo "  cp $HOME/agent/.env $REPO_DIR/.env"
    echo "  nano $REPO_DIR/.env"
fi

# 3. GitHub-Labels anlegen
if [ -f "$REPO_DIR/.env" ]; then
    echo "[2/6] Prüfe GitHub-Labels..."
    set -a && source "$REPO_DIR/.env" && set +a
    python3 "$REPO_DIR/scripts/setup_github.py" 2>/dev/null || echo "  Labels konnten nicht angelegt werden (gh auth prüfen)"
fi

# 4. LiteLLM-Config generieren
echo "[3/6] Generiere LiteLLM-Config..."
python3 "$REPO_DIR/src/litellm_generator.py" --config "$REPO_DIR/crew.yaml" --output "$REPO_DIR/config/litellm.yaml"

# 5. Agent-Templates generieren
echo "[4/6] Generiere Agent-Templates..."
python3 "$REPO_DIR/src/agent_generator.py" --config "$REPO_DIR/crew.yaml" --output "$REPO_DIR/template/.claude"

# 6. systemd-Services installieren
echo "[5/6] Installiere systemd-Services..."
mkdir -p "$HOME/.config/systemd/user"
cp "$REPO_DIR/systemd/litellm.service" "$HOME/.config/systemd/user/"
cp "$REPO_DIR/systemd/orchestrator.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload

# 7. Services starten
echo "[6/6] Starte Services..."
systemctl --user enable litellm.service
systemctl --user enable orchestrator.service
systemctl --user restart litellm.service
sleep 3
systemctl --user restart orchestrator.service

echo ""
echo "=== Installation abgeschlossen ==="
echo "Status:"
systemctl --user status litellm orchestrator --no-pager || true
echo ""
echo "Logs: tail -f $REPO_DIR/logs/orchestrator.log"
echo "Config: nano $REPO_DIR/crew.yaml"

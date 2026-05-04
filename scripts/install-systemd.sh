#!/usr/bin/env bash
# Installiert systemd Services für CodingCrew Microservices
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "=== CodingCrew Microservices systemd Installation ==="

# 1. Verzeichnisse anlegen
echo "[1/4] Erstelle Verzeichnisse..."
mkdir -p "$SYSTEMD_DIR"
mkdir -p "$REPO_DIR/logs"

# 2. Service-Dateien installieren
echo "[2/4] Installiere Service-Dateien..."
for service in api-gateway orchestrator-router worker-ollama worker-claude; do
    sed -e "s|__REPO_DIR__|$REPO_DIR|g" -e "s|__HOME_DIR__|$HOME|g" \
        "$REPO_DIR/systemd/${service}.service" > "$SYSTEMD_DIR/${service}.service"
    echo "  → ${service}.service installiert"
done

# 3. systemd daemon reload
echo "[3/4] Lade systemd Konfiguration..."
systemctl --user daemon-reload

# 4. Services aktivieren (aber nicht starten)
echo "[4/4] Aktiviere Services..."
for service in api-gateway orchestrator-router worker-ollama worker-claude; do
    systemctl --user enable "${service}.service"
done

echo ""
echo "=== Installation abgeschlossen ==="
echo ""
echo "Services starten:"
echo "  systemctl --user start api-gateway.service"
echo "  systemctl --user start orchestrator-router.service"
echo "  systemctl --user start worker-ollama.service"
echo "  systemctl --user start worker-claude.service"
echo ""
echo "Oder alle auf einmal:"
echo "  systemctl --user start api-gateway.service && sleep 2 && systemctl --user start orchestrator-router.service worker-ollama.service worker-claude.service"
echo ""
echo "Status prüfen:"
echo "  systemctl --user status api-gateway.service orchestrator-router.service worker-ollama.service worker-claude.service"
echo ""
echo "Logs live:"
echo "  tail -f $REPO_DIR/logs/*.log"

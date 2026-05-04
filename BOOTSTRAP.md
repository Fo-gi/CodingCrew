# Bootstrap: CodingCrew von 0 auf 1

Komplette Anleitung für ein frisches Server-Setup. Getestet auf Ubuntu 22.04/24.04.

## Voraussetzungen

### Hardware/Software

- Ubuntu 22.04 oder 24.04 (andere Debian-basierte sollten funktionieren)
- Python 3.10+ (`python3 --version`)
- 4 GB RAM minimum (8 GB empfohlen)
- Git

### Externe Dienste

| Dienst | Zweck | Pflicht? |
|--------|-------|----------|
| GitHub | Issues & PRs | Ja |
| Ollama (lokal oder remote) | LLM-Inferenz | Ja* |
| Claude CLI (`claude -p`) | Fallback für komplexe Tasks | Nein |

\* Wenn du kein Ollama hast, kannst du nur Claude CLI verwenden (kostet Geld).

---

## Schritt 1: GitHub Token erstellen

1. Gehe zu https://github.com/settings/tokens/new
2. Wähle **Generate new token (classic)**
3. Scope: `repo` (alles anhaken) + `write:discussion`
4. Token kopieren und sicher speichern

```bash
# Token testen
export GH_TOKEN=ghp_xxx_dein_token_hier
gh auth status
# Sollte "Logged in" anzeigen
```

---

## Schritt 2: Ollama Setup (lokal oder remote)

### Option A: Ollama lokal installieren

```bash
# Ollama installieren
curl -fsSL https://ollama.com/install.sh | sh

# Modelle pullen (empfohlene Kombination)
ollama pull qwen2.5-coder:14b   # Coder-Modell
ollama pull qwen2.5:7b          # Product Owner / QA
ollama pull gemma4:26b          # Code Reviewer (Thinking-Modell)

# Testen
ollama run qwen2.5-coder:14b "Hello World"
```

### Option B: Ollama auf remote Server (via Tailscale)

Wenn Ollama auf einem anderen Rechner läuft (z.B. Windows mit GPU):

1. **Tailscale installieren** auf beiden Rechnern:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   ```

2. **Ollama auf remote Server konfigurieren** (damit er von außen erreichbar ist):
   ```bash
   # Auf dem Ollama-Server (z.B. Windows oder Linux)
   # Umgebungsvariable setzen oder in systemd service eintragen
   OLLAMA_HOST=0.0.0.0:11434
   ```

3. **Tailscale IP herausfinden**:
   ```bash
   # Auf dem Ollama-Server
   hostname -I | grep 100.  # Tailscale IPs beginnen mit 100.
   # Oder:
   tailscale ip
   ```

4. **Verbindung testen** (vom CodingCrew-Server):
   ```bash
   OLLAMA_IP=100.111.112.15  # Deine Tailscale IP hier
   curl -s http://$OLLAMA_IP:11434/api/tags | python3 -m json.tool
   # Sollte Liste der Modelle anzeigen
   ```

---

## Schritt 3: CodingCrew installieren

```bash
# Repo klonen
git clone https://github.com/Fo-gi/CodingCrew.git ~/CodingCrew
cd ~/CodingCrew

# Virtuelle Umgebung erstellen
python3 -m venv .venv
source .venv/bin/activate

# Dependencies installieren (mit requirements.txt)
pip install -r requirements.txt

# ODER mit pyproject.toml (empfohlen)
pip install -e ".[api,dev]"
```

---

## Schritt 4: Konfiguration

### 4.1 `.env` Datei erstellen

```bash
cd ~/CodingCrew
cp .env.example .env
nano .env
```

**Minimale Konfiguration:**

```bash
# GitHub Token (von Schritt 1)
GH_TOKEN=ghp_xxx_dein_token_hier
GITHUB_TOKEN=ghp_xxx_dein_token_hier

# Ollama URL (lokal oder remote)
# Lokal: http://localhost:11434
# Remote (Tailscale): http://100.x.y.z:11434
OLLAMA_TAILSCALE_URL=http://localhost:11434

# Task-Limits
TASK_MAX_ITERS=25
```

### 4.2 `crew.yaml` anpassen

Die Hauptkonfiguration ist in `crew.yaml` (im Root) oder `configs/<projekt>.yaml`.

**Wichtige Einstellungen:**

```yaml
github:
  repo: "Fo-gi/mein-neues-projekt"  # Dein GitHub Repo
  auto_create_repo: true             # Auto-create wenn nicht existent
  auto_create_labels: true           # Auto-create Labels

providers:
  ollama_ts:
    type: ollama
    base_url: "http://localhost:11434"  # Oder Tailscale IP

models:
  junior_dev:
    provider: ollama_ts
    model: qwen2.5-coder:14b  # Muss auf deinem Ollama installiert sein!
    temperature: 0.1
    max_tokens: 4000
```

**Modelle anpassen:**

| Agent | Empfohlenes Modell | Alternative |
|-------|-------------------|-------------|
| `product_owner` | qwen2.5:7b | llama3.2:3b |
| `junior_dev` | qwen2.5-coder:14b | qwen2.5-coder:7b |
| `senior_dev` | qwen2.5-coder:14b | claude-sonnet-4-6 (via Claude CLI) |
| `code_reviewer` | gemma4:26b | qwen2.5-coder:14b |
| `qa_engineer` | qwen2.5:7b | qwen2.5:3b |

### 4.3 GitHub Repo erstellen und Labels anlegen

```bash
cd ~/CodingCrew
source .venv/bin/activate
source .env

# Option A: Automatisch (wenn auto_create_labels=true)
# Das passiert beim ersten Start des Orchestrators
# ODER manuell mit:
python3 scripts/setup_github.py --config crew.yaml

# Option B: Manuell mit gh CLI
REPO=$(grep 'repo:' crew.yaml | head -1 | awk -F'"' '{print $2}')
gh repo create "$REPO" --public --description "CodingCrew Project"

# Labels anlegen
python3 -c "
import subprocess
import yaml

config = yaml.safe_load(open('crew.yaml'))
repo = config['github']['repo']

for tag in config['tags']:
    color = tag.get('color', 'BFD4F2')
    desc = tag.get('description', '')
    cmd = f'gh label create {tag[\"name\"]} --repo {repo} --color {color} --description \"{desc}\"'
    subprocess.run(cmd, shell=True)
"
```

---

## Schritt 5: systemd Services installieren

```bash
cd ~/CodingCrew
bash scripts/install-systemd.sh
```

### Was das Script macht

1. Erstellt systemd Service-Dateien in `~/.config/systemd/user/`
2. Setzt Pfade und Umgebungsvariablen
3. Startet die Services neu

### Services manuell installieren (falls Script fehlt)

```bash
# API Gateway
cat > ~/.config/systemd/user/api-gateway.service << 'EOF'
[Unit]
Description=CodingCrew API Gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/fogi/CodingCrew
Environment="HOME=/home/fogi"
Environment="XDG_CONFIG_HOME=/home/fogi/.config"
Environment="PYTHONPATH=/home/fogi/CodingCrew"
EnvironmentFile=/home/fogi/CodingCrew/.env
ExecStart=/home/fogi/CodingCrew/.venv/bin/uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

# Orchestrator Router
cat > ~/.config/systemd/user/orchestrator-router.service << 'EOF'
[Unit]
Description=CodingCrew Orchestrator Router
After=network.target
After=api-gateway.service

[Service]
Type=simple
WorkingDirectory=/home/fogi/CodingCrew
Environment="HOME=/home/fogi"
Environment="XDG_CONFIG_HOME=/home/fogi/.config"
Environment="PYTHONPATH=/home/fogi/CodingCrew"
EnvironmentFile=/home/fogi/CodingCrew/.env
ExecStart=/home/fogi/CodingCrew/.venv/bin/python3 -m orchestrator.router --project default --poll-interval 30
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

# Ollama Worker
cat > ~/.config/systemd/user/worker-ollama.service << 'EOF'
[Unit]
Description=CodingCrew Ollama Worker
After=network.target
After=orchestrator-router.service

[Service]
Type=simple
WorkingDirectory=/home/fogi/CodingCrew
Environment="HOME=/home/fogi"
Environment="XDG_CONFIG_HOME=/home/fogi/.config"
Environment="PYTHONPATH=/home/fogi/CodingCrew"
EnvironmentFile=/home/fogi/CodingCrew/.env
ExecStartPre=/bin/sleep 5
ExecStart=/home/fogi/CodingCrew/.venv/bin/python3 -c "import asyncio; from workers.ollama_worker import OllamaWorker; from src.models import CrewConfig; from pathlib import Path; config = CrewConfig.load(Path.home() / 'CodingCrew' / 'crew.yaml'); worker = OllamaWorker('junior_dev', config); asyncio.run(worker.run())"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

# Services neu laden
systemctl --user daemon-reload
```

---

## Schritt 6: Services starten

```bash
# Alle Services starten
systemctl --user start api-gateway.service
systemctl --user start orchestrator-router.service
systemctl --user start worker-ollama.service

# Status prüfen
systemctl --user status api-gateway.service
systemctl --user status orchestrator-router.service
systemctl --user status worker-ollama.service

# Logs live verfolgen
tail -f ~/CodingCrew/logs/*.log
```

### Services automatisch beim Boot starten

```bash
# linger aktivieren (damit Services auch ohne Login laufen)
loginctl enable-linger $(whoami)

# Services enable
systemctl --user enable api-gateway.service
systemctl --user enable orchestrator-router.service
systemctl --user enable worker-ollama.service
```

---

## Schritt 7: Ersten Test durchführen

### 7.1 API Health Check

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# Sollte {"status": "healthy"} anzeigen
```

### 7.2 Queue Stats

```bash
curl -s http://localhost:8000/api/v1/queue/stats | python3 -m json.tool
```

### 7.3 Erstes Issue erstellen

1. Gehe zu deinem GitHub Repo: https://github.com/Fo-gi/mein-neues-projekt
2. Erstelle ein neues Issue mit:
   - **Titel**: "Test: Hello World API"
   - **Body**:
     ```markdown
     # SPEC: Hello World API

     ## Goal
     Erstelle eine minimale Flask-App mit einem /hello Endpoint.

     ## Acceptance Criteria
     - [ ] Flask-App mit /hello Endpoint der JSON zurückgibt
     - [ ] Tests mit pytest
     - [ ] README.md mit Setup-Anleitung
     - [ ] requirements.txt
     - [ ] Alle Tests passieren: pytest -q
     - [ ] Lint clean: ruff check .

     ## Out of Scope
     - Datenbank
     - Deployment
     ```
   - **Label**: `agent-ready`

### 7.4 Warten und Logs beobachten

```bash
# Orchestrator Logs
tail -f ~/CodingCrew/logs/orchestrator-router.log

# Worker Logs
tail -f ~/CodingCrew/logs/worker-ollama.log

# In einem anderen Terminal: Watch worktrees
watch -n 2 'ls -la ~/CodingCrew/worktrees/'
```

### 7.5 Ergebnis prüfen

Nach 2-5 Minuten (abhängig von Modell und Task):

```bash
# GitHub PRs checken
gh pr list --repo Fo-gi/mein-neues-projekt

# Oder im Browser: https://github.com/Fo-gi/mein-neues-projekt/pulls
```

---

## Troubleshooting

### Ollama nicht erreichbar

```bash
# Verbindung testen
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# Wenn remote (Tailscale):
curl -s http://100.x.y.z:11434/api/tags

# Firewall prüfen (auf Ollama-Server)
sudo ufw status
# Port 11434 muss offen sein
```

### Worker crasht sofort

```bash
# Logs checken
journalctl --user -u worker-ollama.service --since "5 minutes ago"

# Manuell testen
cd ~/CodingCrew
source .venv/bin/activate
source .env
python3 -c "
from workers.ollama_worker import OllamaWorker
from src.models import CrewConfig
from pathlib import Path
config = CrewConfig.load(Path.home() / 'CodingCrew' / 'crew.yaml')
worker = OllamaWorker('junior_dev', config)
print('Worker created successfully')
"
```

### ModuleNotFoundError

```bash
# Dependencies neu installieren
cd ~/CodingCrew
source .venv/bin/activate
pip install fastapi uvicorn pydantic pyyaml requests python-dotenv

# Cache leeren
find ~/CodingCrew -name "*.pyc" -delete
find ~/CodingCrew -type d -name "__pycache__" -exec rm -rf {} +
```

### Services starten nicht

```bash
# systemd Logs
journalctl --user -u api-gateway.service --since "1 hour ago"
journalctl --user -u orchestrator-router.service --since "1 hour ago"
journalctl --user -u worker-ollama.service --since "1 hour ago"

# Config validieren
cd ~/CodingCrew
source .venv/bin/activate
python3 -c "from src.models import CrewConfig; CrewConfig.load('crew.yaml'); print('Config OK')"
```

### GitHub API Errors

```bash
# Token prüfen
gh auth status

# Token erneuern
gh auth refresh

# Repo existiert?
gh repo view Fo-gi/mein-neues-projekt
```

---

## Nächste Schritte nach erfolgreichem Test

1. **Labels anpassen** in `crew.yaml` für deinen Workflow
2. **Echte Issues** erstellen mit `agent-idea` oder `agent-ready`
3. **Monitoring einrichten** (optional)
4. **Slack-Notifications** konfigurieren (optional)

---

## Nützliche Befehle

```bash
# Alle Services neu starten
systemctl --user restart api-gateway.service orchestrator-router.service worker-ollama.service

# Services stoppen
systemctl --user stop api-gateway.service orchestrator-router.service worker-ollama.service

# Queue leeren
curl -X POST http://localhost:8000/api/v1/queue/purge

# Stuck Issue befreien (Ghost-Label entfernen)
gh issue edit N --repo Fo-gi/mein-neues-projekt --remove-label agent-working

# Ollama Modelle prüfen
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin)['models']]"
```

# CodingCrew

Autonome Coding-Crew mit Microservices-Architektur. Pollt GitHub-Issues, verteilt sie an Worker (Ollama lokal oder Claude CLI Cloud), und erstellt PRs bei Erfolg.

## Architektur

```
┌─────────────────┐    ┌─────────────────┐
│   API Gateway   │    │   File Queue    │
│   (FastAPI:8000)│───▶│   (JSON Files)  │
└─────────────────┘    └────────┬────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
         ▼                      ▼                      ▼
┌─────────────────┐    ┌─────────────────┐    ┌──────────────┐
│   Orchestrator  │    │   Ollama Worker │    │ Claude Worker│
│     Router      │    │   (direct)      │    │ (claude_cli) │
└─────────────────┘    └─────────────────┘    └──────────────┘
```

**Komponenten:**
- **API Gateway**: FastAPI Server mit Webhooks, Queue-Management, Worker-Monitoring
- **File Queue**: Persistente Queue mit Priority, Retry, Deduplication
- **Orchestrator Router**: Pollt GitHub, erstellt Jobs basierend auf Labels
- **Ollama Worker**: Lokale Modelle (qwen2.5, gemma4, etc.)
- **Claude Worker**: Claude CLI für komplexe Tasks

## Schnellstart

### Installation

```bash
# 1. Repo klonen
git clone https://github.com/Fo-gi/CodingCrew.git ~/CodingCrew
cd ~/CodingCrew

# 2. .env konfigurieren
cp .env.example .env
nano .env  # GITHUB_TOKEN und andere Secrets eintragen

# 3. Dependencies installieren
bash scripts/install.sh

# 4. systemd Services installieren
bash scripts/install-systemd.sh
```

### Services starten

```bash
# Alle Services starten
systemctl --user start api-gateway.service
systemctl --user start orchestrator-router.service
systemctl --user start worker-ollama.service
systemctl --user start worker-claude.service

# Status prüfen
systemctl --user status api-gateway.service

# Logs live
tail -f ~/CodingCrew/logs/*.log
```

### Manuelles Starten (ohne systemd)

```bash
# Terminal 1: API Gateway
bash scripts/run-api.sh

# Terminal 2: Orchestrator
bash scripts/run-orchestrator.sh

# Terminal 3: Ollama Worker
bash scripts/run-worker.sh ollama junior_dev

# Terminal 4: Claude Worker
bash scripts/run-worker.sh claude senior_dev
```

## Konfiguration

### Projekt-Config

Jedes Projekt hat eine eigene Config in `configs/<projekt>.yaml`:

```bash
# Neues Projekt anlegen
cp configs/default.yaml configs/my-project.yaml
nano configs/my-project.yaml  # repo, agents, models anpassen
```

### crew.yaml Sektionen

| Sektion | Zweck |
|---------|-------|
| `github` | Ziel-Repo, Auto-Create Optionen |
| `providers` | Ollama/Anthropic Konfiguration |
| `models` | Modell-Aliase mit Provider + Parametern |
| `agents` | Agenten mit Prompt, Modell, Typ (direct/claude_cli) |
| `tags` | Issue-Labels mit Handler-Agent |
| `limits` | Iterationen, Budget, Timeout |

### Agent-Typen

- **`direct`**: Direkter API-Call (Ollama) — für Planung, Review, QA
- ****`claude_cli`**: Via `claude -p` in Worktree — für Implementation

## Workflow

```
agent-idea → agent-spec → agent-design → agent-ready → agent-review → agent-test → agent-deploy → agent-done
                                                              ↑
                         agent-escalation-1/2 ←───────────────┘
```

| Label | Bedeutung |
|-------|-----------|
| `agent-idea` | Rohe Idee → Product Owner erstellt SPEC |
| `agent-epic` | Große Vision → wird in Teil-Issues zerlegt |
| `agent-ready` | Bereit zur Implementation |
| `agent-review` | Code Review läuft |
| `agent-test` | QA validiert Tests |
| `agent-deploy` | Deployment erfolgt |
| `agent-question` | Agent wartet auf Nutzer-Antwort |
| `agent-stuck` | 3 Versuche fehlgeschlagen → manuell |

## OllamaWorker Code-Generierung

Der `OllamaWorker` (`junior_dev`, `senior_dev`) kann Code generieren und anwenden:

**Prompt-Format für Agents:**
```markdown
OUTPUT FORMAT (wichtig):
Du MUSST Dateien als Markdown-Code-Bloecke zurueckgeben.
Jeder Code-Block repraesentiert EINE Datei oder EINEN Shell-Befehl.

Fuer Dateien: Schreibe in der ERSTEN ZEILE als Kommentar den Dateipfad.
```python
# file: app.py
from flask import Flask
app = Flask(__name__)
```

Fuer Shell-Befehle (Setup, Tests):
```bash
# shell
pip install flask pytest
```
```

**Ablauf:**
1. Ollama generiert Code als Markdown-Code-Blöcke
2. Worker parsed die Blöcke (`# file: path` oder `# shell`)
3. Dateien werden geschrieben, Shell-Befehle ausgeführt
4. `pip install` wird automatisch in ein venv umgeleitet
5. `.venv/`, `__pycache__/`, `.pytest_cache/` werden zu `.gitignore` hinzugefügt
6. Tests (`pytest -q`) und Lint (`ruff check .`) werden ausgeführt
7. Bei Erfolg: Ein Commit mit allen Änderungen

## API Endpoints

```bash
# API Docs
http://localhost:8000/docs

# Health Check
curl http://localhost:8000/health

# Queue Stats
curl http://localhost:8000/api/v1/queue/stats

# Worker Status
curl http://localhost:8000/api/v1/workers

# Projects
curl http://localhost:8000/api/v1/projects
```

## Nützliche Befehle

```bash
# GitHub Setup (Labels anlegen)
python3 scripts/setup_github.py

# Queue purgen
curl -X POST http://localhost:8000/api/v1/queue/purge

# Worker Health aufräumen
curl -X POST http://localhost:8000/api/v1/workers/cleanup

# Stuck-Issue befreien (Ghost-Label)
gh issue edit N --repo "$GITHUB_REPO" --remove-label agent-working
```

## Logs

| Log | Zweck |
|-----|-------|
| `logs/api-gateway.log` | API Requests, Errors |
| `logs/orchestrator-router.log` | Issue-Polling, Job-Erstellung |
| `logs/worker-ollama.log` | Ollama Calls, Ergebnisse |
| `logs/worker-claude.log` | Claude CLI Sessions |
| `logs/issue-N-*.jsonl` | Detail-Log pro Issue |

## Voraussetzungen

| Tool | Zweck |
|------|-------|
| `python3 >= 3.10` | Orchestrator, Worker |
| `gh` CLI | GitHub-Issues & PRs |
| `claude` CLI | Coder-Agent (`claude -p`) |
| `git` | Worktrees |
| Ollama (optional) | Lokale Modelle via Tailscale |

## Projektstruktur

```
CodingCrew/
├── api/                  # FastAPI Gateway
├── workers/              # Worker Implementierungen
├── jobqueue/             # File-based Queue
├── orchestrator/         # Router Service
├── shared/               # Shared Utilities
├── configs/              # Projekt-Konfigurationen
├── scripts/              # Start-Skripte
├── systemd/              # systemd Services
├── src/                  # Legacy: Providers, GitHub, Hooks
├── workspace/            # Geklonte Repos
├── worktrees/            # Aktive Worktrees
└── logs/                 # Log-Dateien
```

## Migration von Monolith

Falls du vom alten `src/orchestrator.py` kommst:

| Alt | Neu |
|-----|-----|
| `src/orchestrator.py` | `orchestrator/router.py` + `workers/*` |
| Direkte Agent-Calls | `OllamaWorker` / `ClaudeWorker` |
| Polling im Loop | Orchestrator + Queue |
| Keine API | FastAPI Gateway |

Siehe `ARCHITECTURE.md` für Details.

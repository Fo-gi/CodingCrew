# CodingCrew Microservices Architektur

## Übersicht

CodingCrew ist ein autonomes Coding-Crew Framework das GitHub Issues automatisch bearbeitet.
Die Architektur ist als **Microservices** aufgebaut, nicht als Monolith.

```
┌─────────────────────────────────────────────────────────────┐
│                     Hetzner Server                          │
│                                                             │
│  ┌─────────────────┐    ┌─────────────────┐                │
│  │   API Gateway   │    │   File Queue    │                │
│  │   (FastAPI:8000)│───▶│   (JSON Files)  │                │
│  └─────────────────┘    └────────┬────────┘                │
│                                  │                          │
│         ┌────────────────────────┼────────────────────┐    │
│         │                        │                    │    │
│         ▼                        ▼                    ▼    │
│  ┌─────────────┐         ┌─────────────┐      ┌──────────┐│
│  │ Orchestrator│         │ Ollama      │      │ Claude   ││
│  │   Router    │         │ Workers     │      │ Workers  ││
│  │             │         │             │      │          ││
│  │ - pollt GH  │         │ - direct    │      │ - claude ││
│  │ - routet    │         │ - lokal     │      │ - cloud  ││
│  │ - queue     │         │ - Ollama    │      │ - CLI    ││
│  └─────────────┘         └─────────────┘      └──────────┘│
└─────────────────────────────────────────────────────────────┘
         ▲
         │ Tailscale
         ▼
┌─────────────────┐
│ Windows Laptop  │
│ (Ollama GPU)    │
└─────────────────┘
```

## Services

### 1. API Gateway (`api/`)

FastAPI Server der als zentrale Schnittstelle dient.

**Endpoints:**
- `GET /api/v1/projects` - Alle Projekte
- `GET /api/v1/workers` - Worker Status
- `GET /api/v1/queue/stats` - Queue Statistiken
- `GET /api/v1/queue/jobs` - Jobs auflisten
- `POST /api/v1/queue/jobs` - Neuen Job enqueue
- `POST /api/v1/webhooks/github` - GitHub Webhook empfangen

**Starten:**
```bash
bash scripts/run-api.sh
# http://localhost:8000/docs
```

### 2. File-based Queue (`queue/`)

Persistente Queue basierend auf JSON-Files.

**Features:**
- Priority-basiert (CRITICAL > HIGH > NORMAL > LOW)
- Retry-Logic mit exponential backoff
- Deduplication
- Lock-Mechanismus (flock)

**Verzeichnisstruktur:**
```
~/CodingCrew/queue/
├── pending/      # Jobs waiting to be processed
├── processing/   # Jobs currently being worked on
├── completed/    # Successfully completed jobs
└── failed/       # Jobs that failed permanently
```

### 3. Workers (`workers/`)

Isolierte Prozesse die Jobs abarbeiten.

**BaseWorker:**
- Health-Check (schreibt heartbeat in ~/CodingCrew/health/)
- Signal-Handling (SIGTERM/SIGINT)
- Retry-Logic
- Job-Status Tracking

**OllamaWorker:**
- Für `type: direct` Agents
- Ruft lokale Ollama-Modelle via HTTP auf
- Kein Cloud-Modell nötig

**ClaudeWorker:**
- Für `type: claude_cli` Agents
- Spawned `claude -p` in Worktree
- Auto-Commit, Test-Check, PR-Erstellung

### 4. Orchestrator Router (`orchestrator/`)

Pollt GitHub Issues und erstellt Jobs in der Queue.

**Aufgaben:**
- Pollt GitHub alle 30 Sekunden
- Erstellt Job für jedes Issue mit passendem Label
- Priorisiert nach Label (agent-question > escalation > ready)
- Dedupliziert (kein doppelter Job für gleiche Issue)

## Projektstruktur

```
CodingCrew/
├── api/                  # FastAPI Gateway
│   ├── app.py
│   └── routes/
│       ├── projects.py
│       ├── workers.py
│       ├── queue.py
│       └── webhooks.py
├── workers/              # Worker Implementierungen
│   ├── base.py           # Abstract BaseWorker
│   ├── ollama_worker.py
│   └── claude_worker.py
├── queue/                # File-based Queue
│   └── manager.py
├── orchestrator/         # Router Service
│   └── router.py
├── shared/               # Shared Utilities
│   ├── __init__.py
│   └── config.py
├── configs/              # Projekt-Konfigurationen
│   └── default.yaml
├── scripts/              # Start-Skripte
│   ├── run-api.sh
│   ├── run-worker.sh
│   └── run-orchestrator.sh
├── src/                  # Legacy Code (kann entfernt werden)
│   ├── providers/
│   ├── github/
│   ├── hooks/
│   └── models.py
└── crew.yaml             # Default Config (wird nach configs/ migriert)
```

## Multi-Projekt Support

Jedes Projekt hat eine eigene Config in `configs/<projekt>.yaml`:

```bash
# Neues Projekt anlegen
cp configs/default.yaml configs/my-project.yaml
nano configs/my-project.yaml  # repo anpassen
```

## Starten

### Alle Services manuell starten

```bash
# Terminal 1: API Gateway
bash scripts/run-api.sh

# Terminal 2: Orchestrator Router
bash scripts/run-orchestrator.sh default

# Terminal 3: Ollama Worker
bash scripts/run-worker.sh ollama junior_dev

# Terminal 4: Claude Worker
bash scripts/run-worker.sh claude senior_dev
```

### Mit systemd (production)

TODO: systemd Services für Microservices erstellen

## Health Monitoring

Worker schreiben Health-Status nach `~/CodingCrew/health/`:

```bash
# Worker Status ansehen
cat ~/CodingCrew/health/*.json | python3 -m json.tool

# API Endpoint
curl http://localhost:8000/api/v1/workers
```

## Queue Monitoring

```bash
# Queue Stats
curl http://localhost:8000/api/v1/queue/stats

# Pending Jobs
curl http://localhost:8000/api/v1/queue/jobs?status=pending

# Job Details
curl http://localhost:8000/api/v1/queue/jobs/<job_id>
```

## GitHub Webhook Setup

Statt Polling kann auch Webhook verwendet werden:

1. Gehe zu GitHub Repo Settings → Webhooks
2. Add webhook:
   - Payload URL: `https://dein-server.com/api/v1/webhooks/github`
   - Content type: `application/json`
   - Secret: `GITHUB_WEBHOOK_SECRET` (in .env setzen)
   - Events: Issues, Issue comments

## Migration von Monolith

Falls du vom alten monolithischen Orchestrator kommst:

| Alt (Monolith) | Neu (Microservices) |
|----------------|---------------------|
| `src/orchestrator.py` | `orchestrator/router.py` + `workers/*` |
| Direct Agent Calls | `OllamaWorker` |
| `claude -p` spawn | `ClaudeWorker` |
| Polling in Loop | Orchestrator + Queue |
| Keine API | FastAPI Gateway |

## Nächste Schritte

- [ ] systemd Services für alle Microservices
- [ ] Graceful Shutdown implementieren
- [ ] Log-Rotation hinzufügen
- [ ] Metriken (Prometheus Exporter)
- [ ] Worker Auto-Scaling (mehr Worker bei hoher Last)

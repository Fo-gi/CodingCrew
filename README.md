# CodingCrew

Autonomer Coding-Orchestrator mit zentraler YAML-Config. Pollt GitHub-Issues, orchestriert Agenten (direkte Ollama-Calls + `claude -p`), erstellt PRs bei Erfolg.

## Schnellstart (neuer Server)

```bash
# 1. Repo klonen
git clone https://github.com/Fo-gi/CodingCrew.git ~/CodingCrew
cd ~/CodingCrew

# 2. .env anlegen und ausfuellen
cp .env.example .env
nano .env          # GH_TOKEN und GITHUB_TOKEN eintragen (gh auth token gibt deinen Token aus)

# 3. Ziel-Repo in crew.yaml setzen
nano crew.yaml     # github.repo anpassen, Ollama-URL pruefen

# 4. Installieren (Dependencies + GitHub-Setup + systemd)
bash scripts/install.sh
```

Nach `install.sh` laeuft der Orchestrator als systemd-User-Service und startet automatisch nach Reboot.

## Voraussetzungen

| Tool | Benoetigt fuer |
|------|----------------|
| `python3 >= 3.10` | Orchestrator |
| `gh` CLI | GitHub-Issues & PRs |
| `claude` CLI | Coder-Agent (`claude -p`) |
| `git` | Worktrees |

```bash
# Claude Code CLI installieren
npm install -g @anthropic-ai/claude-code

# GitHub CLI
sudo apt install gh && gh auth login
```

## Architektur

```
GitHub Issues (Labels)
        |
        v
Orchestrator (Python asyncio, systemd)
        |
        +-- product_owner  (Ollama/Kimi)  -> SPEC schreiben, Epics aufteilen
        +-- senior_dev     (Ollama)        -> komplexe Implementation
        +-- junior_dev     (Ollama)        -> einfache Tasks
        +-- code_reviewer  (Ollama)        -> PR-Review gegen SPEC
        +-- qa_engineer    (Ollama)        -> Tests + Acceptance Criteria
        +-- devops_engineer(Ollama)        -> Deployment
        +-- coder          (claude -p)     -> direkte Implementation via Claude CLI
                |
        Stop-Hook (src/hooks/stop_gate.py)
          exit 0 = Tests gruenn + Diff vorhanden -> PR oeffnen
          exit 2 = Claude macht weiter
                |
        Eskalation: 3 Versuche, dann agent-stuck
```

## crew.yaml

Alles in einer Datei konfiguriert:

| Sektion | Zweck |
|---------|-------|
| `github` | Ziel-Repo, Auto-Create Repo/Labels |
| `providers` | Ollama/Anthropic/OpenAI — base_url oder api_key_env |
| `models` | Alias -> Provider + Modellname + temperature |
| `agents` | Name, Prompt, Modell, Typ (direct / claude_cli) |
| `tags` | Issue-Labels, Prioritaet, Handler-Agent |
| `limits` | Iterationen, Budget, Timeout, max_parallel |

### Agent-Typen

- `direct`: Direkter API-Call (Ollama oder Anthropic SDK) — fuer Planung/Review
- `claude_cli`: Via `claude -p` in einem Worktree — fuer eigentliche Implementierung

## Taeglicher Workflow

```bash
# Issue mit SPEC anlegen
gh issue create --repo dein-org/dein-repo --label agent-ready --editor

# Orchestrator-Status
systemctl --user status orchestrator

# Live-Log
tail -f ~/CodingCrew/logs/orchestrator.log

# Pause / Weiter
systemctl --user stop orchestrator
systemctl --user start orchestrator
```

## SPEC.md-Vorlage

```markdown
# SPEC: <kurze Beschreibung>

## Goal
<ein Absatz>

## Acceptance criteria (machine-checkable)
- [ ] ...
- [ ] All tests pass: `pytest -q`
- [ ] Lint clean: `ruff check .`

## Out of scope
- ...

## Budget
max_iterations: 25
max_usd: 8
```

## Nuetzliche Befehle

```bash
# Config validieren (gibt JSON aus)
python3 -m src.config

# Einzelner Durchlauf ohne Loop
python3 -m src.orchestrator --once

# GitHub-Setup wiederholen (Labels neu anlegen)
python3 scripts/setup_github.py

# Stuck-Issue befreien (Ghost-Label)
source .env && gh issue edit N --repo "$GITHUB_TOKEN_REPO" --remove-label agent-working

# Hooks direkt testen
echo '{}' | python3 src/hooks/stop_gate.py; echo "Exit: $?"
```

## Kein LiteLLM

- Direkte HTTP-Calls zu Ollama (`/api/chat`)
- SDK oder HTTP zu Anthropic
- Kein Proxy-Port 4000 noetig

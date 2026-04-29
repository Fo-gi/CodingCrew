# CodingCrew

Autonome Coding-Crew mit zentraler YAML-Config. Der Orchestrator polled GitHub-Issues, zerlegt Epics, und startet Claude (`claude -p`) mit konfigurierbaren Agenten.

## Architektur

```
GitHub Issues (Label "agent-ready")
        │
        ▼
Orchestrator (Python, systemd)
        │
        ├── Epic Planner (lokales LLM) → neue Issues
        ├── Coder (claude -p) → Commits
        ├── Tester (lokales LLM) → Test-Report
        └── Reviewer (lokales LLM) → Review-Report
                │
        Stop-Hook: exit 0 = fertig + PR
                │
        Eskalation: 3 Versuche, dann agent-stuck
```

## Schnellstart

```bash
# 1. Repo klonen
git clone https://github.com/Fo-gi/CodingCrew.git ~/CodingCrew
cd ~/CodingCrew

# 2. Config anpassen
cp crew.yaml crew.yaml.local
nano crew.yaml.local

# 3. GitHub-Labels anlegen
python3 scripts/setup_github.py

# 4. Installieren
bash scripts/install.sh

# 5. Status prüfen
systemctl --user status orchestrator
```

## Config (`crew.yaml`)

Eine Datei, alles konfiguriert:

| Sektion | Zweck |
|---------|-------|
| `github` | Ziel-Repo, Label-Auto-Create |
| `tags` | Issue-Labels, Priorität, Handler-Agent |
| `providers` | Ollama, Anthropic, OpenAI, Gemini |
| `models` | Alias → Provider + Modellname |
| `agents` | Name, Prompt, Modell, Tools |
| `limits` | Iterationen, Budget, Timeout, Parallelität |
| `litellm` | Proxy-Port, Routing, Fallbacks, Budget |

## Täglicher Workflow

```bash
# Issue anlegen
gh issue create --repo Fo-gi/ProjectBlue --label agent-ready --editor

# Über Nacht: Orchestrator arbeitet ab

# Morgens: PRs reviewen, mergen
```

## Nützliche Befehle

```bash
# Logs live
tail -f ~/CodingCrew/logs/orchestrator.log

# Einzelnen Durchlauf (ohne Loop)
python3 -m src.orchestrator --once

# LiteLLM-Config neu generieren
python3 -m src.litellm_generator

# Config validieren
python3 -m src.config
```

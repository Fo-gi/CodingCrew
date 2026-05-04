# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An autonomous coding crew orchestrator with a "company structure": Product Owner, Senior Dev, Junior Dev, Code Reviewer, QA Engineer, DevOps Engineer. Each role is a configured agent with its own model and prompt. The orchestrator polls GitHub Issues, moves them through a workflow pipeline, and opens PRs on success. No LiteLLM proxy — direct provider calls only.

## Architecture

### Single source of truth: `crew.yaml`

All behavior is declared in one YAML file and loaded into Pydantic models (`src/models.py`). The config validates cross-references: a `model` must reference an existing `provider`, and an `agent` must reference an existing `model`. If validation fails at load time, the orchestrator refuses to start.

```
crew.yaml
  ├── github         repo name, auto_create_repo, auto_create_labels
  ├── providers      ollama/anthropic/openai/gemini with base_url or api_key_env
  ├── models         alias → provider + model name + temperature/max_tokens
  ├── agents         name → model + type (direct | claude_cli) + prompt + tools
  ├── tags           issue labels with priority, color, and handler agent
  └── limits         iterations, budget, timeout, max_parallel
```

### Company structure (agents)

| Agent | Model | Type | Role |
|-------|-------|------|------|
| `product_owner` | coder-local (qwen2.5-coder:14b) | direct | Research, SPEC writing, epic breakdown |
| `senior_dev` | coder-local | direct | Complex implementation, architecture, escalation |
| `junior_dev` | coder-local | direct | Simple bugs, docs, small features |
| `code_reviewer` | planner-local (gemma4:26b) | direct | PR review against SPEC |
| `qa_engineer` | qa-local (qwen2.5:7b) | direct | Test validation, acceptance criteria |
| `devops_engineer` | devops-local (qwen2.5-coder:7b) | direct | CI/CD, deployment |

**Model fallback**: `kimi-local` (kimi-k2.6:cloud) wurde aufgrund instabiler JSON-Ausgaben durch `coder-local` ersetzt.

### Workflow pipeline (tags)

Issues move through stages in priority order (lower = earlier):

```
agent-idea → agent-spec → agent-design → agent-ready → agent-review → agent-test → agent-deploy → agent-done
```

- `agent-idea` (priority 1): Raw idea. Product Owner researches and writes SPEC.
- `agent-spec` (priority 4): SPEC written, waiting for design.
- `agent-design` (priority 5): Senior Dev creates architecture.
- `agent-ready` (priority 6): Junior Dev implements simple tasks.
- `agent-ready-complex` (priority 7): Senior Dev implements complex tasks.
- `agent-review` (priority 8): Code Reviewer checks PR.
- `agent-test` (priority 9): QA Engineer validates tests.
- `agent-deploy` (priority 10): DevOps Engineer deploys.
- `agent-question` (priority 0): Agent asked a question, waiting for human reply.
- `agent-escalation-1` (priority 11): First attempt failed, Senior Dev retries.
- `agent-escalation-2` (priority 12): Second attempt failed, Senior Dev deep-review.
- `agent-stuck` (priority 0): All 3 attempts failed, manual intervention needed.

### Tag → Agent → Model → Provider

1. The orchestrator polls GitHub for issues matching tags in `crew.yaml`
2. Tags are processed in `priority` order (lower = earlier)
3. The tag's `handler` names an agent from `crew.yaml`
4. The agent's `model` names a model, which names a provider
5. `direct` agents call the provider's `chat()` method; `claude_cli` agents spawn `claude -p`

### Question system

When an agent needs clarification (missing config, ambiguous requirements):
1. Agent posts a comment with the question
2. Label changes to `agent-question`
3. Human replies in the comment
4. Orchestrator detects the new reply and moves label back to `agent-ready`

### Provider layer

`src/providers/base.py` defines `BaseProvider.chat(model, messages, temperature, max_tokens)`. Concrete implementations:
- `OllamaProvider`: POST to `base_url/api/chat`, cleans `ollama_chat/` prefixes
- `AnthropicProvider`: tries SDK first, falls back to HTTP (`x-api-key` header)
- `openai` and `gemini` providers are declared in `ProviderType` but not yet implemented

### Orchestrator lifecycle

`src/orchestrator.py` runs an asyncio loop:
1. `run_once()` fetches `origin/main`, cleans ghost `agent-working` labels
2. Checks `agent-question` issues first (highest priority — human is waiting)
3. Tags sorted by priority; for each tag with a handler, list matching issues
4. The oldest issue is processed under an `asyncio.Semaphore(max_parallel)`
5. `direct` agents run via `_run_direct_agent()` — calls provider.chat()
6. `claude_cli` agents spawn `claude -p` in a git worktree with `SPEC.md` injected
7. On success → push branch, open PR, label moves to next stage (e.g. `agent-ready` → `agent-review`)
8. On failure → escalate: `agent-escalation-1` → `agent-escalation-2` → `agent-stuck`

### OllamaWorker Code-Generation

`workers/ollama_worker.py` implements code generation for `junior_dev` and `senior_dev` agents:

1. **Parse**: Markdown code blocks are parsed for `# file:` and `# shell` markers
2. **Apply**: Files are written, shell commands executed (pip → venv)
3. **Gitignore**: `.venv/`, `__pycache__/`, `.pytest_cache/` auto-added
4. **Test**: `pytest -q` runs in venv
5. **Lint**: `ruff check .` runs in venv
6. **Commit**: Single commit on success

**Prompt format** (in `crew.yaml` / `configs/*.yaml`):
```markdown
OUTPUT FORMAT (wichtig):
Du MUSST Dateien als Markdown-Code-Bloecke zurueckgeben.
Fuer Dateien: Schreibe in der ERSTEN ZEILE als Kommentar den Dateipfad.
```python
# file: app.py
from flask import Flask
```
```bash
# shell
pip install flask pytest
```
```

### Hooks

Both hooks are command-line programs that Claude Code executes via `settings.json` hooks:
- `src/hooks/guard.py`: PreToolUse hook. Blocks dangerous Bash patterns (`rm -rf /`, `sudo`, `curl | bash`, force-push, SQL drops)
- `src/hooks/stop_gate.py`: Stop hook. Counts iterations (hard cap from `TASK_MAX_ITERS`), then checks pytest, ruff, and diff vs `origin/main`. Exit 0 = done (orchestrator opens PR); exit 2 = Claude continues with stderr as feedback

### GitHub setup

`src/github/setup.py` creates the repo (`gh repo create`) and labels (`gh label create`) if `auto_create_repo` / `auto_create_labels` are true. Called once at orchestrator startup.

## Commands

```bash
# Validate config (prints JSON)
python3 -m src.config

# Run orchestrator once (no daemon loop)
python3 -m src.orchestrator --once

# Setup GitHub repo + labels manually
python3 scripts/setup_github.py

# Lint
ruff check . && ruff format .

# Run all tests
pytest -q

# Run a single test file
pytest tests/test_something.py -q

# Install / reinstall
bash scripts/install.sh

# systemd controls
systemctl --user status orchestrator.service
systemctl --user restart orchestrator.service
systemctl --user stop orchestrator.service

# Watch logs live
tail -f ~/CodingCrew/logs/orchestrator.log

# Debug a specific issue run (while it's running)
tail -f ~/CodingCrew/logs/issue-N-*.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        if d.get('type') == 'assistant':
            for b in d['message']['content']:
                if b.get('type') == 'text': print(b['text'][:120])
    except: pass
"

# Check Ollama models are reachable
curl -s http://100.111.112.15:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin)['models']]"

# Fix stuck issue (remove ghost agent-working label)
gh issue edit N --repo $(grep 'repo:' crew.yaml | head -1 | awk -F'"' '{print $2}') --remove-label agent-working
```

## Important non-obvious details

- **No LiteLLM**: Direct HTTP to Ollama, SDK/HTTP to Anthropic. No `localhost:4000` proxy. Ollama lives on a Windows laptop via Tailscale at `http://100.111.112.15:11434` (configured in `crew.yaml`)
- **Ollama timeout**: Default was 180s, raised to 300s for kimi-k2.6:cloud which needs longer for JSON generation
- **Thinking models**: `gemma4:26b` is a thinking model. Its response may be in `reasoning_content` rather than `content`. The provider layer handles this in `_clean_content()`. However, gemma4:26b does NOT reliably return JSON for the code_reviewer prompt — fallback skips review instead of looping
- **Epic JSON parsing**: The naive regex `\[.*?\]` matched `[]` from Markdown checkboxes before the real JSON array. Fixed with a robust parser that tries `content.startswith('[')` first, then falls back to searching from the last `]` backward
- **Label cleanup on success**: `agent-ready` was NOT being removed when an issue succeeded — causing the same issue to be picked up again. Fixed: `remove = ["agent-working", "agent-ready", "agent-ready-complex"]`
- **PR already exists**: If a reviewed issue gets sent back to `agent-ready` and re-implements, `gh pr create` fails because the branch already has a PR. Fixed: check `gh pr view branch` before creating
- **Agent state in worktrees**: Each run creates `~/CodingCrew/worktrees/agent/issue-N/`. Inside that worktree, `.agent/iter` tracks how many Stop-Hook iterations have run
- **Git identity in worktrees**: The orchestrator sets `user.email = agent@localhost` and `user.name = "Claude Agent"` inside each worktree so commits don't fail
- **Ghost label cleanup**: If an issue has `agent-working` but no log file was written in the last 5 minutes, the label is removed automatically
- **Environment**: Orchestrator reads `.env` via `EnvironmentFile` in systemd. Required vars: `GITHUB_TOKEN`, and provider keys (e.g. `ANTHROPIC_API_KEY`) depending on config
- **Worktree reuse on escalation**: If `agent-escalation-1` or `-2` exists and the branch already exists, the orchestrator reuses the existing worktree (does not reset to `origin/main`). `ESCALATION.md` is committed into the branch
- **Question handling**: The orchestrator checks `agent-question` issues first by looking at the latest comment author. If the last comment is from a human (not `github-actions[bot]` or `Claude Agent`), it moves the label back to `agent-ready`
- **install.sh**: Uses `sed` to substitute `__REPO_DIR__` and `__HOME_DIR__` in the systemd template file, then does `stop + sleep 2 + start` instead of `restart` to avoid race conditions
- **systemd double-start guard**: The orchestrator uses a lockfile at `~/CodingCrew/.orchestrator.lock` (flock) to prevent multiple instances. There was a bug where `systemctl --user restart` started a second process before the first exited — fixed by using `stop + sleep + start`

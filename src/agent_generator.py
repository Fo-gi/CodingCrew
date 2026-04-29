#!/usr/bin/env python3
"""Generiert .claude/agents/*.md aus crew.yaml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import load_config


def generate_agents(config, output_dir: Path | str = "template/.claude/agents") -> list[Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    generated = []
    for name, agent in config.agents.items():
        if agent.type.value == "claude_cli":
            continue  # claude_cli Agents haben kein .md File

        md = f"""---
name: {name}
description: {agent.description}
tools: {', '.join(agent.tools)}
---
{agent.prompt}
"""
        path = out / f"{name}.md"
        path.write_text(md)
        generated.append(path)

    return generated


def generate_settings(config, output_dir: Path | str = "template/.claude") -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    settings = {
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": [
                "Bash(pytest:*)",
                "Bash(npm test:*)",
                "Bash(npm run:*)",
                "Bash(ruff:*)",
                "Bash(mypy:*)",
                "Bash(git add:*)",
                "Bash(git commit:*)",
                "Bash(git push:*)",
                "Bash(git diff:*)",
                "Bash(git log:*)",
                "Bash(git status:*)",
                "Bash(gh pr:*)",
                "Bash(gh issue:*)",
                "Bash(curl http://localhost:4000/*)",
            ],
            "deny": [
                "Bash(rm -rf:*)",
                "Bash(sudo:*)",
                "Bash(curl * | bash:*)",
                "Bash(curl * | sh:*)",
                "Read(./.env)",
                "Read(./.env.*)",
                "Write(./.env)",
                "Write(./.env.*)",
            ],
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "python3 ~/CodingCrew/src/hooks/guard.py"}]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit|MultiEdit",
                    "hooks": [{"type": "command",
                        "command": "ruff format \"$CLAUDE_TOOL_INPUT_FILE_PATH\" 2>/dev/null; prettier --write \"$CLAUDE_TOOL_INPUT_FILE_PATH\" 2>/dev/null; true"}]
                }
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": "python3 ~/CodingCrew/src/hooks/stop_gate.py"}]}
            ],
        }
    }

    import json
    path = out / "settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return path


def main():
    parser = argparse.ArgumentParser(description="Generiere Agent-Templates aus crew.yaml")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--output", "-o", default="template/.claude", help="Ausgabe-Verzeichnis")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from config import load_config

    cfg = load_config(args.config)
    agents = generate_agents(cfg, Path(args.output) / "agents")
    settings = generate_settings(cfg, args.output)

    print(f"Agent-Templates: {len(agents)}")
    for a in agents:
        print(f"  {a.name}")
    print(f"settings.json: {settings}")


if __name__ == "__main__":
    main()

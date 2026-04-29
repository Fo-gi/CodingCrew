#!/usr/bin/env python3
"""Blockiert offensichtlich gefaehrliche Bash-Calls."""
import json, sys, re

inp = json.load(sys.stdin)
if inp.get("tool_name") != "Bash":
    sys.exit(0)

cmd = inp.get("tool_input", {}).get("command", "")

DANGER = [
    r"\brm\s+-rf\s+/(?:\s|$)",     # rm -rf /
    r"\brm\s+-rf\s+~",              # rm -rf ~
    r"\brm\s+-rf\s+/home",         # rm -rf /home
    r"\brm\s+-rf\s+~/agent",       # rm -rf ~/agent
    r"\bsudo\b",                    # kein sudo
    r"\bdd\s+if=.*of=/dev/",        # dd auf raw-device
    r":\(\)\s*\{.*:\|:&\s*\};:",    # fork-bomb
    r"\bgit\s+push\s+.*--force\b",  # force-push
    r"\bDROP\s+(TABLE|DATABASE)\b", # SQL-drops
    r">\s*/etc/",                   # in /etc schreiben
    r"\bcurl\s.*\|\s*(sudo\s+)?(bash|sh)", # curl | bash
]
for pat in DANGER:
    if re.search(pat, cmd, re.IGNORECASE):
        print(f"[guard] BLOCKED dangerous command: {cmd[:120]}", file=sys.stderr)
        sys.exit(1)   # blockt den Tool-Call
sys.exit(0)

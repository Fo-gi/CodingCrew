#!/usr/bin/env python3
"""
Stop-Hook: laeuft jedesmal wenn Claude meint er sei fertig.
- Prueft Tests/Lint/Diff
- Wenn alles gruen UND es gibt nen Diff: exit 0 (Loop endet, Orchestrator macht PR)
- Wenn nicht: schreibt Anweisung nach stderr und exit 2
  -> Claude bekommt stderr als next-Input und macht weiter
- Hartes Cap: Iterationen + Dollar-Budget
"""
import json, os, subprocess, sys, pathlib

inp = json.load(sys.stdin)
if inp.get("stop_hook_active"):           # Re-Entry-Schutz
    sys.exit(0)

cwd = pathlib.Path.cwd()
state = cwd / ".agent"
state.mkdir(exist_ok=True)

iter_file = state / "iter"

n = int(iter_file.read_text() or 0) + 1 if iter_file.exists() else 1
iter_file.write_text(str(n))

MAX_ITERS = int(os.environ.get("TASK_MAX_ITERS", 25))

if n > MAX_ITERS:
    print(f"[stop_gate] Iteration cap {MAX_ITERS} erreicht. Stoppe.", file=sys.stderr)
    sys.exit(0)

def find_pytest():
    for candidate in [cwd / ".venv/bin/pytest", cwd / "venv/bin/pytest"]:
        if candidate.exists():
            return str(candidate)
    r = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True)
    if r.returncode == 0:
        return [sys.executable, "-m", "pytest"]
    return ["pytest"]

def run(*args):
    return subprocess.run(args, capture_output=True, text=True)

# Tests: venv-pytest hat Vorrang, sonst npm test
tests_ok = True; tests_msg = ""
if (cwd / "pyproject.toml").exists() or (cwd / "pytest.ini").exists() or (cwd / "tests").is_dir():
    pytest_cmd = find_pytest()
    r = run(*pytest_cmd, "-q", "--tb=line")
    if r.returncode != 0:
        tests_ok = False
        tests_msg = f"pytest failures:\n{r.stdout[-2000:]}"
elif (cwd / "package.json").exists():
    r = run("npm", "test", "--silent")
    if r.returncode != 0:
        tests_ok = False
        tests_msg = f"npm test failures:\n{r.stdout[-2000:]}"

# Lint: nur wenn config existiert
lint_ok = True; lint_msg = ""
if (cwd / "ruff.toml").exists() or (cwd / "pyproject.toml").exists():
    r = run("ruff", "check", ".")
    if r.returncode != 0:
        lint_ok = False
        lint_msg = f"ruff issues:\n{r.stdout[-1000:]}"

# Diff vs main: gibt's ueberhaupt Aenderungen?
# Erst pruefen ob origin/main existiert (koennte beim ersten Run fehlen)
r_check = run("git", "rev-parse", "origin/main")
if r_check.returncode == 0:
    r = run("git", "diff", "--quiet", "origin/main")
    has_diff = (r.returncode != 0)
else:
    has_diff = False

if tests_ok and lint_ok and has_diff:
    sys.exit(0)        # Fertig — orchestrate.sh macht PR

# Sonst: Claude weitermachen lassen
msgs = []
if not tests_ok: msgs.append(tests_msg)
if not lint_ok:  msgs.append(lint_msg)
if not has_diff: msgs.append("Du hast noch keine Aenderungen vs origin/main gemacht. Implementiere die Spec.")
print("\n\n".join(msgs), file=sys.stderr)
sys.exit(2)

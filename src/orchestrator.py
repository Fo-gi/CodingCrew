#!/usr/bin/env python3
"""Python-Orchestrator fuer CodingCrew.

Liest Issues aus GitHub, orchestriert Agenten via claude -p,
und erstellt PRs bei Erfolg.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from config import load_config


LOCKFILE = Path("/tmp/codingcrew-orchestrator.lock")


def acquire_lock() -> bool:
    """Nur eine Instanz erlauben."""
    fd = os.open(str(LOCKFILE), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_NB | fcntl.LOCK_EX)
        os.write(fd, str(os.getpid()).encode())
        return True
    except (IOError, OSError):
        os.close(fd)
        return False


class GitHubClient:
    def __init__(self, repo: str):
        self.repo = repo

    def _gh(self, *args: str) -> str:
        r = subprocess.run(
            ["gh", *args, "--repo", self.repo],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh failed: {r.stderr}")
        return r.stdout

    def list_issues(self, label: str, exclude: list[str] | None = None) -> list[dict]:
        search = " ".join(f"-label:{e}" for e in (exclude or []))
        out = self._gh(
            "issue", "list", "--label", label,
            "--search", search,
            "--json", "number,title,body,labels",
            "--limit", "50",
        )
        return json.loads(out)

    def edit_labels(self, num: int, add: list[str] | None = None, remove: list[str] | None = None) -> None:
        cmd = ["issue", "edit", str(num)]
        for a in add or []:
            cmd += ["--add-label", a]
        for r in remove or []:
            cmd += ["--remove-label", r]
        self._gh(*cmd)

    def create_pr(self, base: str, head: str, title: str, body: str) -> str:
        out = self._gh(
            "pr", "create",
            "--base", base, "--head", head,
            "--title", title,
            "--body", body,
        )
        return out.strip()


class Orchestrator:
    def __init__(self, config_path: str | None = None):
        self.cfg = load_config(config_path)
        self.gh = GitHubClient(self.cfg.github.repo)
        self.log_dir = Path.home() / "CodingCrew" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_base = Path.home() / "CodingCrew" / "worktrees"
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self.workspace = Path.home() / "CodingCrew" / "workspace" / self.cfg.github.repo.split("/")[-1]
        self.workspace.parent.mkdir(parents=True, exist_ok=True)

        self._ensure_workspace()

    def _ensure_workspace(self):
        if not self.workspace.exists():
            subprocess.run(
                ["gh", "repo", "clone", self.cfg.github.repo, str(self.workspace)],
                check=True,
            )

    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[{ts}] {msg}")

    def _notify(self, msg: str):
        self._log(f"[notify] {msg}")
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if webhook:
            subprocess.run(
                ["curl", "-s", "-X", "POST",
                 "-H", "Content-type: application/json",
                 "--data", json.dumps({"text": msg}),
                 webhook],
                capture_output=True,
            )

    def _check_cost(self):
        key = os.environ.get(self.cfg.litellm.master_key_env, "")
        if not key:
            return
        try:
            r = subprocess.run(
                ["curl", "-s", f"http://localhost:{self.cfg.litellm.port}/global/spend",
                 "-H", f"Authorization: Bearer {key}"],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(r.stdout)
            spend = data.get("spend", 0)
            cap = self.cfg.limits.daily_budget_usd
            self._log(f"[cost] Heute: ${spend:.2f} / ${cap:.2f}")
            if spend > cap * 0.8:
                self._notify(f":warning: Tageslimit zu 80% ausgeschöpft (${spend:.2f}).")
        except Exception as e:
            self._log(f"[cost] Fehler: {e}")

    def _cleanup_ghost_labels(self):
        """Entfernt verwaiste agent-working Labels."""
        try:
            issues = self.gh.list_issues("agent-working")
            for issue in issues:
                num = issue["number"]
                # Prüfe ob ein Log-File in den letzten 5 Minuten geschrieben wurde
                pattern = f"issue-{num}-*.jsonl"
                logs = list(self.log_dir.glob(pattern))
                recent = any(
                    (datetime.now(timezone.utc).timestamp() - l.stat().st_mtime) < 300
                    for l in logs
                )
                if not recent:
                    self._log(f"Ghost-Label entfernt bei #{num}")
                    self.gh.edit_labels(num, remove=["agent-working"])
        except Exception as e:
            self._log(f"[ghost] Fehler: {e}")

    def _ollama_analyze(self, log_file: Path, spec_file: Path, attempt: int) -> str:
        """Ruft Ollama für Fehleranalyse auf."""
        try:
            failures = []
            last_result = None
            for line in log_file.read_text(errors="ignore").splitlines():
                try:
                    d = json.loads(line)
                    if d.get("type") == "result":
                        last_result = d
                    if d.get("type") == "system" and "error" in d.get("subtype", ""):
                        failures.append(str(d.get("error", ""))[:300])
                except json.JSONDecodeError:
                    pass
            if last_result:
                r = last_result.get("result", "")
                if r:
                    failures.append(f"Letztes Claude-Ergebnis: {str(r)[:500]}")
            failures_text = "\n".join(failures[-5:]) or "Keine expliziten Fehler im Log."
            spec_summary = spec_file.read_text(errors="ignore")[:2000]

            payload = json.dumps({
                "model": "qwen-local",
                "messages": [
                    {"role": "system", "content": "Du bist ein Senior-Softwareingenieur. Analysiere fehlgeschlagene Implementierungen knapp und präzise. Antworte auf Deutsch."},
                    {"role": "user", "content": f"""Versuch {attempt} einer Implementierung ist fehlgeschlagen.\n\nSPEC (Auszug):\n{spec_summary}\n\nFehler/Letzte Ausgabe:\n{failures_text}\n\nBitte liefere:\n1. Ursachenanalyse (2-3 Sätze)\n2. Konkrete Korrekturen (nummerierte Liste)\n3. Muster die vermieden werden sollen\n\nSei präzise und umsetzbar."""}
                ],
                "max_tokens": 1500,
                "temperature": 0.1,
            })
            key = os.environ.get(self.cfg.litellm.master_key_env, "")
            r = subprocess.run(
                ["curl", "-s", f"http://localhost:{self.cfg.litellm.port}/v1/chat/completions",
                 "-H", f"Authorization: Bearer {key}",
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=120,
            )
            data = json.loads(r.stdout)
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            return content.strip()
        except Exception as e:
            return f"Automatische Analyse nicht verfügbar: {e}"

    def _handle_epic(self, issue: dict):
        """Zerlegt Epic in agent-ready Issues."""
        num = issue["number"]
        title = issue["title"]
        body = issue["body"]
        self._notify(f":brain: Epic #{num} wird geplant...")
        self.gh.edit_labels(num, add=["agent-working"])

        # Prompt via Temp-Datei (Emojis/Sonderzeichen sicher)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(body)
            tmp = f.name

        prompt = (
            "Break down this project vision into 5-8 GitHub issues to implement sequentially. "
            "Rules: foundation first, each issue self-contained with acceptance criteria, "
            "include full vision as context in each body.\n\n"
            "Return ONLY a JSON array. Each element: {\"title\": string, \"body\": markdown string}. "
            "No explanation, no code fences.\n\nVision:\n" + body
        )
        payload = json.dumps({
            "model": "tester-local",
            "messages": [
                {"role": "system", "content": "You are a software project planner. Output only valid JSON arrays."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 6000,
            "temperature": 0.1,
        })
        key = os.environ.get(self.cfg.litellm.master_key_env, "")
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "180",
                 f"http://localhost:{self.cfg.litellm.port}/v1/chat/completions",
                 "-H", f"Authorization: Bearer {key}",
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=200,
            )
            data = json.loads(r.stdout)
            msg = data["choices"][0]["message"]
            content = msg.get("content", "") or msg.get("reasoning_content", "")
            content = content.strip()
            content = re.sub(r"^```(json)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                issues = json.loads(match.group())
                total = len(issues)
                for i, item in enumerate(issues, 1):
                    ititle = f"[{i}/{total}] {item['title']}"
                    subprocess.run(
                        ["gh", "issue", "create", "--repo", self.cfg.github.repo,
                         "--title", ititle, "--body", item["body"], "--label", "agent-ready"],
                        capture_output=True, text=True,
                    )
                self._log(f"Epic #{num} in {total} Issues aufgeteilt.")
            else:
                self._log(f"Epic #{num}: Keine JSON-Antwort gefunden.")
        except Exception as e:
            self._log(f"Epic #{num} Planung fehlgeschlagen: {e}")
        finally:
            os.unlink(tmp)
            self.gh.edit_labels(num, remove=["agent-working"], add=["agent-done"])

    def _build_prompt(self, spec: str, agent: str, attempt: int, esc_level: int) -> str:
        """Baut den Prompt für claude -p."""
        agent_cfg = self.cfg.agents.get(agent)
        if not agent_cfg:
            raise ValueError(f"Unbekannter Agent: {agent}")

        base = agent_cfg.prompt + "\n\n"
        base += f"Hard cap: {self.cfg.limits.max_iterations} iterations, ${self.cfg.limits.task_budget_usd}.\n"

        if esc_level == 1:
            base += (
                "\nWICHTIG (Versuch 2/3): Ein erster Versuch ist fehlgeschlagen.\n"
                "Lies ESCALATION.md — dort steht die Analyse von Ollama.\n"
                "Behebe die dort beschriebenen Probleme gezielt.\n"
            )
        elif esc_level == 2:
            base += (
                "\nKRITISCH (Versuch 3/3, letzter automatischer Versuch):\n"
                "Zwei vorherige Versuche sind fehlgeschlagen. Lies ESCALATION.md sorgfältig.\n"
                "Gehe systematisch vor: prüfe jeden Schritt explizit, mache keine Annahmen.\n"
                "Lasse Tests immer direkt nach jeder Änderung laufen.\n"
            )

        return base

    def _check_success(self, wt: Path) -> bool:
        """Prüft ob Tests grün und Diff vorhanden."""
        # Diff
        r = subprocess.run(
            ["git", "diff", "--quiet", "origin/main"],
            cwd=wt, capture_output=True,
        )
        if r.returncode == 0:
            self._log("[check] Kein Diff vs origin/main")
            return False

        # Python-Tests
        if (wt / "pyproject.toml").exists() or (wt / "pytest.ini").exists() or (wt / "tests").is_dir():
            pytest_bin = None
            for candidate in [wt / ".venv/bin/pytest", wt / "venv/bin/pytest"]:
                if candidate.exists():
                    pytest_bin = str(candidate)
                    break
            if not pytest_bin:
                r = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True)
                if r.returncode == 0:
                    pytest_bin = f"{sys.executable} -m pytest"

            if pytest_bin:
                r = subprocess.run(
                    pytest_bin.split() + ["-q", "--tb=line"],
                    cwd=wt, capture_output=True,
                )
                if r.returncode != 0:
                    self._log("[check] pytest fehlgeschlagen")
                    return False

        # Node.js-Tests
        if (wt / "package.json").exists() and (wt / "node_modules").is_dir():
            r = subprocess.run(
                ["npm", "test", "--silent"],
                cwd=wt, capture_output=True,
            )
            if r.returncode != 0:
                self._log("[check] npm test fehlgeschlagen")
                return False

        return True

    def _run_claude(self, wt: Path, prompt: str, log: Path) -> int:
        """Startet claude -p und gibt Exit-Code zurück."""
        r = subprocess.run(
            [
                "timeout", str(self.cfg.limits.timeout_minutes * 60),
                "claude", "-p", prompt,
                "--model", "sonnet",
                "--output-format", "stream-json",
                "--verbose",
                "--permission-mode", "acceptEdits",
                "--max-turns", "200",
            ],
            cwd=wt,
            stdout=open(log, "w"),
            stderr=subprocess.STDOUT,
        )
        return r.returncode

    def _process_issue(self, issue: dict):
        """Verarbeitet ein einzelnes Issue."""
        num = issue["number"]
        title = issue["title"]
        body = issue["body"]
        labels = {l["name"] for l in issue.get("labels", [])}

        # Eskalationslevel bestimmen
        esc_level = 0
        if "agent-escalation-2" in labels:
            esc_level = 2
        elif "agent-escalation-1" in labels:
            esc_level = 1

        attempt = esc_level + 1
        self._notify(f":robot_face: Picking up #{num} (Versuch {attempt}/3): {title}")
        self.gh.edit_labels(num, add=["agent-working"])

        # Agent bestimmen
        handler = None
        for tag in self.cfg.tags:
            if tag.name in labels and tag.handler:
                handler = tag.handler
                break
        if not handler:
            handler = "coder"

        branch = f"agent/issue-{num}"
        wt = self.worktree_base / branch

        # Worktree aufbauen
        if wt.exists():
            subprocess.run(["rm", "-rf", str(wt)], check=True)
        subprocess.run(
            ["git", "-C", str(self.workspace), "worktree", "prune"],
            check=True,
        )

        if esc_level > 0 and subprocess.run(
            ["git", "-C", str(self.workspace), "rev-parse", "--verify", branch],
            capture_output=True,
        ).returncode == 0:
            subprocess.run(
                ["git", "-C", str(self.workspace), "worktree", "add", str(wt), branch],
                check=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(self.workspace), "worktree", "add", "-B", branch, str(wt), "origin/main"],
                check=True,
            )

        # Git-Identity
        subprocess.run(["git", "config", "user.email", "agent@localhost"], cwd=wt, check=True)
        subprocess.run(["git", "config", "user.name", "Claude Agent"], cwd=wt, check=True)

        # SPEC.md und Agent-State
        (wt / "SPEC.md").write_text(body)
        agent_dir = wt / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / "iter").write_text("0")

        # Agent-Template kopieren
        template_claude = Path.home() / "CodingCrew" / "template" / ".claude"
        if not (wt / ".claude").exists() and template_claude.exists():
            subprocess.run(["cp", "-r", str(template_claude), str(wt / ".claude")], check=True)

        # Prompt bauen
        prompt = self._build_prompt(body, handler, attempt, esc_level)
        log = self.log_dir / f"issue-{num}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

        # Claude starten
        ccode = self._run_claude(wt, prompt, log)

        # Erfolgs-Check
        if self._check_success(wt):
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=wt, capture_output=True,
            )
            iter_count = (wt / ".agent" / "iter").read_text().strip()
            pr_body = (
                f"Closes #{num}.\n\n"
                f"Versuch: {attempt}/3\n"
                f"Iterationen: {iter_count}\n"
                f"Log: {log.name}"
            )
            try:
                pr_url = self.gh.create_pr("main", branch, f"[agent] {title}", pr_body)
            except RuntimeError:
                pr_url = "PR konnte nicht erstellt werden"

            # Labels aufräumen
            remove = ["agent-working"]
            if esc_level >= 1:
                remove.append("agent-escalation-1")
            if esc_level >= 2:
                remove.append("agent-escalation-2")
            self.gh.edit_labels(num, add=["agent-done"], remove=remove)
            self._notify(f":white_check_mark: #{num} fertig (Versuch {attempt}) -> {pr_url}")
        else:
            # Eskalation
            if esc_level < 2:
                self._notify(f":mag: #{num} Versuch {attempt} fehlgeschlagen — Ollama analysiert...")
                analysis = self._ollama_analyze(log, wt / "SPEC.md", attempt)
                esc_file = wt / "ESCALATION.md"
                esc_file.write_text(
                    f"# Eskalations-Analyse — Versuch {attempt}\n\n"
                    f"Generiert von Ollama nach fehlgeschlagenem Versuch.\n\n"
                    f"{analysis}\n\n---\n*Log: {log.name}*\n"
                )
                subprocess.run(
                    ["git", "add", "ESCALATION.md"], cwd=wt, capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"chore: Ollama-Analyse nach Versuch {attempt}"],
                    cwd=wt, capture_output=True,
                )

            if esc_level == 0:
                self.gh.edit_labels(num, remove=["agent-working"], add=["agent-escalation-1"])
                self._notify(f":warning: #{num} Versuch 1 fehlgeschlagen. Eskalation 1...")
            elif esc_level == 1:
                self.gh.edit_labels(num, remove=["agent-working", "agent-escalation-1"], add=["agent-escalation-2"])
                self._notify(f":warning: #{num} Versuch 2 fehlgeschlagen. Eskalation 2...")
            else:
                self.gh.edit_labels(num, remove=["agent-working", "agent-escalation-2"], add=["agent-stuck"])
                self._notify(f":sos: #{num} nach 3 Versuchen nicht lösbar. Manuelle Intervention erforderlich.")

        # Worktree aufräumen
        subprocess.run(
            ["git", "-C", str(self.workspace), "worktree", "remove", "--force", str(wt)],
            capture_output=True,
        )

    def run_once(self):
        """Ein einzelner Durchlauf."""
        # Workspace aktualisieren
        subprocess.run(
            ["git", "-C", str(self.workspace), "fetch", "origin", "main", "--quiet"],
            capture_output=True,
        )

        self._check_cost()
        self._cleanup_ghost_labels()

        # Tags nach Priorität sortieren
        sorted_tags = sorted(self.cfg.tags, key=lambda t: t.priority)

        for tag in sorted_tags:
            if tag.priority == 0:
                continue  # agent-working, agent-done, agent-stuck haben keine Handler
            if not tag.handler:
                continue

            exclude = ["agent-working", "agent-done", "agent-stuck"]
            # Bei Eskalationen: jeweils die anderen Eskalationslabels ausschließen
            if tag.name == "agent-escalation-1":
                exclude += ["agent-escalation-2"]
            elif tag.name == "agent-escalation-2":
                exclude += ["agent-escalation-1"]

            try:
                issues = self.gh.list_issues(tag.name, exclude=exclude)
            except RuntimeError:
                continue

            if issues:
                issue = sorted(issues, key=lambda i: i["number"])[0]

                if tag.name == "agent-epic":
                    self._handle_epic(issue)
                else:
                    self._process_issue(issue)
                return True

        return False

    def run(self):
        """Endlos-Loop."""
        if not acquire_lock():
            self._log("Bereits eine Instanz aktiv. Beende.")
            sys.exit(1)

        self._log("Orchestrator gestartet.")
        while True:
            try:
                processed = self.run_once()
                if not processed:
                    time.sleep(60)
                else:
                    time.sleep(5)
            except Exception as e:
                self._log(f"Fehler im Loop: {e}")
                time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="CodingCrew Orchestrator")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--once", action="store_true", help="Nur ein Durchlauf, kein Loop")
    args = parser.parse_args()

    orch = Orchestrator(args.config)
    if args.once:
        processed = orch.run_once()
        sys.exit(0 if processed else 0)
    else:
        orch.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Python-Orchestrator fuer CodingCrew.

Liest Issues aus GitHub, orchestriert Agenten direkt (kein LiteLLM),
und erstellt PRs bei Erfolg.

Workflow:
  agent-idea -> agent-research -> agent-spec -> agent-design -> agent-ready -> agent-review -> agent-test -> agent-deploy
  agent-question: Blockiert bis Nutzer antwortet
  agent-escalation-1/2: Senior Dev uebernimmt
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_config
from src.github import GitHubClient, GitHubSetup
from src.providers import get_model_client


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

        self._semaphore = asyncio.Semaphore(self.cfg.limits.max_parallel)
        self._ensure_workspace()

        # Mapping: Tag -> naechster Tag im Workflow
        self._workflow_next = {
            "agent-idea": "agent-spec",
            "agent-research": "agent-spec",
            "agent-spec": "agent-design",
            "agent-design": "agent-ready",
            "agent-ready": "agent-review",
            "agent-ready-complex": "agent-review",
            "agent-review": "agent-test",
            "agent-test": "agent-deploy",
            "agent-deploy": "agent-done",
        }

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

    def _cleanup_ghost_labels(self):
        """Entfernt verwaiste agent-working Labels."""
        try:
            issues = self.gh.list_issues("agent-working")
            for issue in issues:
                num = issue["number"]
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

    # ------------------------------------------------------------------
    # Direkte Agent-Calls (fuer lokale Modelle)
    # ------------------------------------------------------------------
    async def _run_direct_agent(self, issue: dict, agent_name: str) -> str:
        """Fuehrt einen direct-Agent aus und gibt das Ergebnis zurueck."""
        agent_cfg = self.cfg.agents.get(agent_name)
        if not agent_cfg:
            raise ValueError(f"Unbekannter Agent: {agent_name}")

        model_cfg = self.cfg.models.get(agent_cfg.model)
        if not model_cfg:
            raise ValueError(f"Unbekanntes Modell: {agent_cfg.model}")

        provider = get_model_client(agent_cfg.model, self.cfg)

        messages = [
            {"role": "system", "content": f"Du bist {agent_name}. {agent_cfg.description}"},
            {"role": "user", "content": f"Issue: {issue['title']}\n\n{issue['body']}"}
        ]

        self._log(f"[direct] {agent_name} arbeitet an #{issue['number']} mit {agent_cfg.model}...")
        content = await asyncio.to_thread(
            provider.chat,
            model=model_cfg.model,
            messages=messages,
            temperature=model_cfg.temperature,
            max_tokens=model_cfg.max_tokens,
        )
        return content

    async def _handle_question(self, issue: dict):
        """Prueft ob bei einem agent-question Issue neue Kommentare vom Nutzer sind."""
        num = issue["number"]
        self._log(f"[question] Pruefe #{num} auf Antwort...")

        try:
            # Letzten Kommentar abrufen
            r = subprocess.run(
                ["gh", "issue", "view", str(num), "--repo", self.cfg.github.repo,
                 "--json", "comments", "--comments"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                return

            data = json.loads(r.stdout)
            comments = data.get("comments", [])

            if not comments:
                return

            # Letzter Kommentar
            last_comment = comments[-1]
            author = last_comment.get("author", {}).get("login", "")

            # Pruefen ob es ein Agent-Kommentar ist (dann ignorieren)
            if author in ["github-actions[bot]", "Claude Agent"]:
                return

            # Nutzer hat geantwortet! Label zuruecksetzen
            self._log(f"[question] Nutzer hat bei #{num} geantwortet.")
            self.gh.edit_labels(num, remove=["agent-question"], add=["agent-ready"])
            self._notify(f":speech_balloon: #{num}: Frage beantwortet. Weiter mit Implementation.")

        except Exception as e:
            self._log(f"[question] Fehler bei #{num}: {e}")

    async def _handle_idea(self, issue: dict):
        """Product Owner: Idee -> Recherche oder direkt SPEC."""
        num = issue["number"]
        self._notify(f":bulb: Idee #{num} wird analysiert...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "product_owner")

            # Wenn der Product Owner Fragen hat (erkennbar an "Frage:" oder "?")
            if "frage" in result.lower() or "?" in result[:200]:
                # Kommentar mit Frage posten
                subprocess.run(
                    ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                     "--body", f"## Frage vom Product Owner\n\n{result}"],
                    capture_output=True,
                )
                self.gh.edit_labels(num, remove=["agent-working", "agent-idea"], add=["agent-question"])
                self._notify(f":question: #{num}: Product Owner hat Fragen gestellt.")
            else:
                # SPEC als Kommentar posten und weiter zu agent-spec
                subprocess.run(
                    ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                     "--body", f"## SPEC (vom Product Owner)\n\n{result}"],
                    capture_output=True,
                )
                self.gh.edit_labels(num, remove=["agent-working", "agent-idea"], add=["agent-spec"])
                self._notify(f":page_facing_up: #{num}: SPEC erstellt. Weiter mit Design.")
        except Exception as e:
            self._log(f"[idea] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_epic(self, issue: dict):
        """Product Owner: Epic zerlegen."""
        num = issue["number"]
        self._notify(f":brain: Epic #{num} wird geplant...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "product_owner")

            content = re.sub(r"^```(json)?\n?", "", result)
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
                self.gh.edit_labels(num, remove=["agent-working", "agent-epic"], add=["agent-done"])
            else:
                self._log(f"Epic #{num}: Keine JSON-Antwort gefunden.")
                self.gh.edit_labels(num, remove=["agent-working"])
        except Exception as e:
            self._log(f"Epic #{num} Planung fehlgeschlagen: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_research(self, issue: dict):
        """Product Owner: Recherche durchfuehren."""
        num = issue["number"]
        self._notify(f":mag: Recherche fuer #{num}...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "product_owner")

            subprocess.run(
                ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                 "--body", f"## Recherche-Ergebnisse\n\n{result}"],
                capture_output=True,
            )
            self.gh.edit_labels(num, remove=["agent-working", "agent-research"], add=["agent-spec"])
            self._notify(f":page_facing_up: #{num}: Recherche fertig. SPEC erstellt.")
        except Exception as e:
            self._log(f"[research] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_spec(self, issue: dict):
        """Product Owner: SPEC finalisieren."""
        num = issue["number"]
        self._notify(f":page_facing_up: SPEC fuer #{num} wird finalisiert...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "product_owner")

            subprocess.run(
                ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                 "--body", f"## Final SPEC\n\n{result}"],
                capture_output=True,
            )
            self.gh.edit_labels(num, remove=["agent-working", "agent-spec"], add=["agent-design"])
            self._notify(f":art: #{num}: SPEC fertig. Weiter mit Design.")
        except Exception as e:
            self._log(f"[spec] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_design(self, issue: dict):
        """Senior Dev: Design/Architektur erstellen."""
        num = issue["number"]
        self._notify(f":art: Design fuer #{num} wird erstellt...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            # Design kann auch lokal laufen wenn es nicht zu komplex ist
            result = await self._run_direct_agent(issue, "senior_dev")

            subprocess.run(
                ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                 "--body", f"## Design/Architektur\n\n{result}"],
                capture_output=True,
            )
            self.gh.edit_labels(num, remove=["agent-working", "agent-design"], add=["agent-ready"])
            self._notify(f":rocket: #{num}: Design fertig. Bereit zur Implementation.")
        except Exception as e:
            self._log(f"[design] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_review(self, issue: dict):
        """Code Reviewer: PR reviewen."""
        num = issue["number"]
        self._notify(f":eyes: Code Review fuer #{num}...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "code_reviewer")

            # JSON parsen
            json_match = re.search(r"\{.*\}", result, re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                approve = review.get("approve", False)

                if approve:
                    subprocess.run(
                        ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                         "--body", f"## Code Review: APPROVED :white_check_mark:\n\n{result}"],
                        capture_output=True,
                    )
                    self.gh.edit_labels(num, remove=["agent-working", "agent-review"], add=["agent-test"])
                    self._notify(f":white_check_mark: #{num}: Code Review bestanden.")
                else:
                    blocking = review.get("blocking", [])
                    subprocess.run(
                        ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                         "--body", f"## Code Review: CHANGES REQUESTED :x:\n\n**Blocking:**\n{chr(10).join('- ' + b for b in blocking)}\n\n{result}"],
                        capture_output=True,
                    )
                    self.gh.edit_labels(num, remove=["agent-working", "agent-review"], add=["agent-ready"])
                    self._notify(f":x: #{num}: Code Review hat Blocker. Zurueck zur Implementation.")
            else:
                self._log(f"Review #{num}: Kein JSON gefunden.")
                self.gh.edit_labels(num, remove=["agent-working"])
        except Exception as e:
            self._log(f"[review] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_test(self, issue: dict):
        """QA Engineer: Tests validieren."""
        num = issue["number"]
        self._notify(f":test_tube: QA fuer #{num}...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "qa_engineer")

            json_match = re.search(r"\{.*\}", result, re.DOTALL)
            if json_match:
                qa = json.loads(json_match.group())
                approve = qa.get("approve", False)

                if approve:
                    subprocess.run(
                        ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                         "--body", f"## QA: PASSED :white_check_mark:\n\n{result}"],
                        capture_output=True,
                    )
                    self.gh.edit_labels(num, remove=["agent-working", "agent-test"], add=["agent-deploy"])
                    self._notify(f":white_check_mark: #{num}: QA bestanden. Bereit fuer Deployment.")
                else:
                    criteria = qa.get("criteria_missing", [])
                    subprocess.run(
                        ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                         "--body", f"## QA: FAILED :x:\n\n**Fehlende Criteria:**\n{chr(10).join('- ' + c for c in criteria)}\n\n{result}"],
                        capture_output=True,
                    )
                    self.gh.edit_labels(num, remove=["agent-working", "agent-test"], add=["agent-ready"])
                    self._notify(f":x: #{num}: QA hat Fehler. Zurueck zur Implementation.")
            else:
                self._log(f"QA #{num}: Kein JSON gefunden.")
                self.gh.edit_labels(num, remove=["agent-working"])
        except Exception as e:
            self._log(f"[test] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    async def _handle_deploy(self, issue: dict):
        """DevOps Engineer: Deployment."""
        num = issue["number"]
        self._notify(f":rocket: Deployment fuer #{num}...")
        self.gh.edit_labels(num, add=["agent-working"])

        try:
            result = await self._run_direct_agent(issue, "devops_engineer")

            subprocess.run(
                ["gh", "issue", "comment", str(num), "--repo", self.cfg.github.repo,
                 "--body", f"## Deployment\n\n{result}"],
                capture_output=True,
            )
            self.gh.edit_labels(num, remove=["agent-working", "agent-deploy"], add=["agent-done"])
            self._notify(f":rocket: #{num}: Deployment abgeschlossen!")
        except Exception as e:
            self._log(f"[deploy] Fehler bei #{num}: {e}")
            self.gh.edit_labels(num, remove=["agent-working"])

    # ------------------------------------------------------------------
    # Claude CLI Agent (fuer Cloud-Modelle)
    # ------------------------------------------------------------------
    def _build_prompt(self, spec: str, agent: str, attempt: int, esc_level: int) -> str:
        """Baut den Prompt fuer claude -p."""
        agent_cfg = self.cfg.agents.get(agent)
        if not agent_cfg:
            raise ValueError(f"Unbekannter Agent: {agent}")

        base = agent_cfg.prompt + "\n\n"
        base += f"Hard cap: {self.cfg.limits.max_iterations} iterations, ${self.cfg.limits.task_budget_usd}.\n"

        if esc_level == 1:
            base += (
                "\nWICHTIG (Versuch 2/3): Ein erster Versuch ist fehlgeschlagen.\n"
                "Lies ESCALATION.md — dort steht die Analyse.\n"
                "Behebe die dort beschriebenen Probleme gezielt.\n"
            )
        elif esc_level == 2:
            base += (
                "\nKRITISCH (Versuch 3/3, letzter automatischer Versuch):\n"
                "Zwei vorherige Versuche sind fehlgeschlagen. Lies ESCALATION.md sorgfaeltig.\n"
                "Gehe systematisch vor: pruefe jeden Schritt explizit, mache keine Annahmen.\n"
                "Lasse Tests immer direkt nach jeder Aenderung laufen.\n"
            )

        return base

    def _check_success(self, wt: Path) -> bool:
        """Prueft ob Tests gruen und Diff vorhanden."""
        r = subprocess.run(
            ["git", "diff", "--quiet", "origin/main"],
            cwd=wt, capture_output=True,
        )
        if r.returncode == 0:
            self._log("[check] Kein Diff vs origin/main")
            return False

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

        if (wt / "package.json").exists() and (wt / "node_modules").is_dir():
            r = subprocess.run(
                ["npm", "test", "--silent"],
                cwd=wt, capture_output=True,
            )
            if r.returncode != 0:
                self._log("[check] npm test fehlgeschlagen")
                return False

        return True

    async def _run_claude(self, wt: Path, prompt: str, log: Path) -> int:
        """Startet claude -p und gibt Exit-Code zurueck."""
        proc = await asyncio.create_subprocess_exec(
            "timeout", str(self.cfg.limits.timeout_minutes * 60),
            "claude", "-p", prompt,
            "--model", "sonnet",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "acceptEdits",
            "--max-turns", "200",
            cwd=str(wt),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        log.write_bytes(stdout)
        return proc.returncode or 0

    async def _process_issue(self, issue: dict):
        """Verarbeitet ein einzelnes Implementierungs-Issue."""
        num = issue["number"]
        title = issue["title"]
        body = issue["body"]
        labels = {l["name"] for l in issue.get("labels", [])}

        esc_level = 0
        if "agent-escalation-2" in labels:
            esc_level = 2
        elif "agent-escalation-1" in labels:
            esc_level = 1

        attempt = esc_level + 1
        self._notify(f":robot_face: Picking up #{num} (Versuch {attempt}/3): {title}")
        self.gh.edit_labels(num, add=["agent-working"])

        handler = None
        for tag in self.cfg.tags:
            if tag.name in labels and tag.handler:
                handler = tag.handler
                break
        if not handler:
            handler = "senior_dev"

        branch = f"agent/issue-{num}"
        wt = self.worktree_base / branch

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

        subprocess.run(["git", "config", "user.email", "agent@localhost"], cwd=wt, check=True)
        subprocess.run(["git", "config", "user.name", "Claude Agent"], cwd=wt, check=True)

        (wt / "SPEC.md").write_text(body)
        agent_dir = wt / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / "iter").write_text("0")

        prompt = self._build_prompt(body, handler, attempt, esc_level)
        log = self.log_dir / f"issue-{num}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

        ccode = await self._run_claude(wt, prompt, log)

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

            remove = ["agent-working"]
            if esc_level >= 1:
                remove.append("agent-escalation-1")
            if esc_level >= 2:
                remove.append("agent-escalation-2")
            self.gh.edit_labels(num, add=["agent-review"], remove=remove)
            self._notify(f":white_check_mark: #{num} fertig (Versuch {attempt}) -> {pr_url}")
        else:
            if esc_level < 2:
                self._notify(f":mag: #{num} Versuch {attempt} fehlgeschlagen — Analyse...")
                analysis = await self._ollama_analyze(log, wt / "SPEC.md", attempt)
                esc_file = wt / "ESCALATION.md"
                esc_file.write_text(
                    f"# Eskalations-Analyse — Versuch {attempt}\n\n"
                    f"Generiert nach fehlgeschlagenem Versuch.\n\n"
                    f"{analysis}\n\n---\n*Log: {log.name}*\n"
                )
                subprocess.run(
                    ["git", "add", "ESCALATION.md"], cwd=wt, capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"chore: Analyse nach Versuch {attempt}"],
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
                self._notify(f":sos: #{num} nach 3 Versuchen nicht loesbar. Manuelle Intervention erforderlich.")

        subprocess.run(
            ["git", "-C", str(self.workspace), "worktree", "remove", "--force", str(wt)],
            capture_output=True,
        )

    async def _ollama_analyze(self, log_file: Path, spec_file: Path, attempt: int) -> str:
        """Ruft direkt Ollama fuer Fehleranalyse auf."""
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

            messages = [
                {"role": "system", "content": "Du bist ein Senior-Softwareingenieur. Analysiere fehlgeschlagene Implementierungen knapp und praezise. Antworte auf Deutsch."},
                {"role": "user", "content": f"""Versuch {attempt} einer Implementierung ist fehlgeschlagen.

SPEC (Auszug):
{spec_summary}

Fehler/Letzte Ausgabe:
{failures_text}

Bitte liefere:
1. Ursachenanalyse (2-3 Saetze)
2. Konkrete Korrekturen (nummerierte Liste)
3. Muster die vermieden werden sollen

Sei praezise und umsetzbar."""}
            ]

            model_cfg = self.cfg.models.get("qwen-local")
            if not model_cfg:
                return "Kein qwen-local Modell konfiguriert."
            provider = get_model_client("qwen-local", self.cfg)
            content = await asyncio.to_thread(
                provider.chat,
                model=model_cfg.model,
                messages=messages,
                temperature=model_cfg.temperature,
                max_tokens=model_cfg.max_tokens,
            )
            return content.strip()
        except Exception as e:
            return f"Automatische Analyse nicht verfuegbar: {e}"

    # ------------------------------------------------------------------
    # Haupt-Loop
    # ------------------------------------------------------------------
    async def _run_tag(self, tag) -> bool:
        """Verarbeitet alle Issues eines Tags."""
        if tag.priority == 0 or not tag.handler:
            return False

        exclude = ["agent-working", "agent-done", "agent-stuck"]
        if tag.name == "agent-escalation-1":
            exclude += ["agent-escalation-2"]
        elif tag.name == "agent-escalation-2":
            exclude += ["agent-escalation-1"]

        try:
            issues = self.gh.list_issues(tag.name, exclude=exclude)
        except RuntimeError:
            return False

        if not issues:
            return False

        issue = sorted(issues, key=lambda i: i["number"])[0]

        async with self._semaphore:
            if tag.name == "agent-idea":
                await self._handle_idea(issue)
            elif tag.name == "agent-epic":
                await self._handle_epic(issue)
            elif tag.name == "agent-research":
                await self._handle_research(issue)
            elif tag.name == "agent-spec":
                await self._handle_spec(issue)
            elif tag.name == "agent-design":
                await self._handle_design(issue)
            elif tag.name == "agent-review":
                await self._handle_review(issue)
            elif tag.name == "agent-test":
                await self._handle_test(issue)
            elif tag.name == "agent-deploy":
                await self._handle_deploy(issue)
            elif tag.name in ("agent-ready", "agent-ready-complex"):
                await self._process_issue(issue)
        return True

    async def run_once(self) -> bool:
        """Ein einzelner Durchlauf."""
        subprocess.run(
            ["git", "-C", str(self.workspace), "fetch", "origin", "main", "--quiet"],
            capture_output=True,
        )

        self._cleanup_ghost_labels()

        # 1. Rückfragen prüfen (höchste Priorität - Nutzer wartet)
        try:
            questions = self.gh.list_issues("agent-question")
            if questions:
                question = sorted(questions, key=lambda i: i["number"])[0]
                await self._handle_question(question)
                return True
        except RuntimeError:
            pass

        # 2. Normale Tags abarbeiten
        sorted_tags = sorted(self.cfg.tags, key=lambda t: t.priority)

        for tag in sorted_tags:
            if await self._run_tag(tag):
                return True

        return False

    async def run(self):
        """Endlos-Loop."""
        if not acquire_lock():
            self._log("Bereits eine Instanz aktiv. Beende.")
            sys.exit(1)

        self._log("Orchestrator gestartet.")

        if self.cfg.github.auto_create_repo or self.cfg.github.auto_create_labels:
            setup = GitHubSetup(self.cfg)
            try:
                setup.setup()
            except Exception as e:
                self._log(f"[setup] GitHub-Setup fehlgeschlagen: {e}")

        while True:
            try:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(5)
            except Exception as e:
                self._log(f"Fehler im Loop: {e}")
                await asyncio.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="CodingCrew Orchestrator")
    parser.add_argument("--config", "-c", default=None, help="Pfad zu crew.yaml")
    parser.add_argument("--once", action="store_true", help="Nur ein Durchlauf, kein Loop")
    args = parser.parse_args()

    orch = Orchestrator(args.config)
    if args.once:
        processed = asyncio.run(orch.run_once())
        sys.exit(0 if processed else 0)
    else:
        asyncio.run(orch.run())


if __name__ == "__main__":
    main()

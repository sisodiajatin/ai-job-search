"""Nightly drafting for a Hermes Agent cron job running in `--no-agent` mode (no LLM is involved).

It runs exactly one fixed command, `queue_cli.py --json`, and prints a short summary for Hermes to deliver.
It never passes --fill-forms (that opens Chrome windows and needs your desktop), never submits, and never prints raw logs.

Hermes treats empty output as "nothing to report", so an empty queue stays silent. Exit code 0 = fine (including "nothing to do" and
"queue busy"); 1 = something is wrong and Hermes should raise a failure notice.

Override the interpreters with JOBDESK_PYTHON / JOBDESK_CLAUDE_BIN if they live elsewhere on this machine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_TIMEOUT_SECONDS = 12 * 60 * 60  #the runner has its own per-job timeout; this only guards against a hang
REASON_LIMIT = 140
KNOWN_PYTHON = Path(r"C:\Program Files\Python313\python.exe")


def find_python() -> str:
    """The system Python that has this repo's dependencies, not whichever interpreter is running this script."""
    override = os.environ.get("JOBDESK_PYTHON")
    if override:
        return override
    return str(KNOWN_PYTHON) if KNOWN_PYTHON.is_file() else (shutil.which("python") or sys.executable)


def find_claude() -> str | None:
    return os.environ.get("JOBDESK_CLAUDE_BIN") or shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude") if p.is_file()), None
    )


def command() -> list[str]:
    """The one command this job is allowed to run. Deliberately no --fill-forms."""
    return [find_python(), str(REPO / "web" / "queue_cli.py"), "--json"]


def auto_queue_enabled() -> bool:
    """Opt-in switch in web/queue_config.json. Off unless you turn it on."""
    try:
        config = json.loads((REPO / "web" / "queue_config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool((config.get("auto_queue") or {}).get("enabled"))


def refresh_enabled() -> bool:
    """Opt-in: scrape and rank fresh listings before queueing (auto_queue.refresh in web/queue_config.json)."""
    try:
        config = json.loads((REPO / "web" / "queue_config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool((config.get("auto_queue") or {}).get("refresh"))


def run_refresh(env: dict) -> list[str]:
    """Scrape new listings and rank them (refresh_cli.py). Returns lines to report; never raises, since the queue run must still happen."""
    try:
        done = subprocess.run([find_python(), str(REPO / "web" / "refresh_cli.py")], cwd=str(REPO), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=2 * 60 * 60)
    except (subprocess.TimeoutExpired, OSError) as err:
        return [f"Refresh failed: {err}"]
    lines = [line for line in (done.stdout or "").strip().splitlines() if line.startswith(("Scrape:", "Ranking:"))]
    return lines if done.returncode == 0 else [f"Refresh failed: {(done.stdout or 'no output').strip().splitlines()[-1][:200]}"]


def approval_enabled() -> bool:
    """Telegram approval is on once its bot config exists (see web/telegram_approval.py)."""
    return (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "jobdesk" / "telegram.json").is_file()


def run_approval(env: dict) -> list[str]:
    """Send the queue to Telegram and wait up to 30 minutes for 'drop 3 5'. Never raises: no answer means draft everything."""
    try:
        done = subprocess.run([find_python(), str(REPO / "web" / "telegram_approval.py"), "--wait", "30"], cwd=str(REPO), env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45 * 60)
    except (subprocess.TimeoutExpired, OSError) as err:
        return [f"Telegram approval failed: {err}"]
    out = (done.stdout or "").strip().splitlines()
    return [out[-1]] if done.returncode == 0 and out else ([] if done.returncode == 0 else [f"Telegram approval failed: {(out or ['no output'])[-1][:200]}"])


def run_auto_queue(env: dict) -> list[str]:
    """Queue the best-fitting jobs first (auto_queue.py, caps from queue_config.json). Returns lines to report, [] when nothing
    was queued. Never raises: a problem here is reported but must not stop the drafting run."""
    try:
        done = subprocess.run([find_python(), str(REPO / "web" / "auto_queue.py"), "--json"], cwd=str(REPO), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
    except (subprocess.TimeoutExpired, OSError) as err:
        return [f"Auto-queue failed: {err}"]
    output = (done.stdout or "").strip()
    if done.returncode != 0:
        return [f"Auto-queue failed: {output.splitlines()[-1] if output else 'no output'}"]
    try:
        queued = json.loads(output.splitlines()[-1]).get("queued") or []
    except (IndexError, json.JSONDecodeError):
        return ["Auto-queue finished, but its summary could not be read."]
    if not queued:
        return []
    return [f"Auto-queued {len(queued)} job(s):"] + [f"  {q.get('fit')} | {q.get('company') or 'Unknown'} | {q.get('title') or ''}" for q in queued]


def format_summary(summary: dict) -> str:
    lines = [f"Job queue run: {summary['drafted']} drafted, {summary['skipped']} skipped, {summary['failed']} failed."]
    for job in summary.get("jobs", []):
        score = f" ({job['score']})" if job.get("score") is not None else ""
        reason = str(job.get("reason") or "").replace("\n", " ")[:REASON_LIMIT]
        lines.append(f"- {job.get('company') or 'Unknown'} | {job.get('title') or ''}: {job.get('status')}{score}" + (f" - {reason}" if reason else ""))
    if summary["drafted"]:
        lines.append("Review the drafts on the dashboard's Jobs page, then use Fill form (or Run queue and fill forms) while you are at the desk.")
    return "\n".join(lines)


def main() -> int:
    env = dict(os.environ)
    claude = find_claude()
    if claude:
        env["JOBDESK_CLAUDE_BIN"] = claude  # a scheduler's PATH often lacks the user's local bin
    queued_lines = (run_refresh(env) if refresh_enabled() else []) + (run_auto_queue(env) if auto_queue_enabled() else [])
    if approval_enabled():
        queued_lines += run_approval(env)

    def say(*lines: str) -> None:
        print("\n".join([*queued_lines, *lines]))

    try:
        done = subprocess.run(command(), cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=RUN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        say("Job queue run did not finish within 12 hours and was stopped.")
        return 1
    except OSError as err:
        say(f"Job queue run could not start: {err}")
        return 1

    output = (done.stdout or "").strip()
    if done.returncode == 3:
        say("Job queue run skipped: the queue is already running (dashboard or another command).")
        return 0
    if done.returncode == 1:
        if "queue is empty" in output.lower():
            if queued_lines:
                say()
            return 0  # nothing queued: stay silent
        say(f"Job queue run could not start: {output.splitlines()[-1] if output else 'no output'}")
        return 1
    if done.returncode != 0:
        say(f"Job queue run ended with exit code {done.returncode}.")
        return 1
    try:
        summary = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        say("Job queue run finished, but its summary could not be read. Check the dashboard.")
        return 1
    say(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

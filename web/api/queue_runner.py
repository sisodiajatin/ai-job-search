"""Work through the apply queue unattended: one `claude -p "/apply <url> --auto"` per job.

Safety notes:
- Each job runs with an explicit tool allowlist and `acceptEdits`, never `bypassPermissions`,
  because the run reads untrusted posting text.
- The job URL is placed in a single argv element (no shell). It comes from our own queue and is
  also checked with the same https-only pattern the terminal launcher uses.
- One job at a time, with a per-job timeout and budget cap; repeated failures stop the run.
- Drafts only. This module never submits an application and never marks one applied.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import finalize, protected
from .launcher import LaunchError, validate_url
from .paths import COVER_DIR, CV_DIR, REPO
from .store import _slug, dequeue_job, list_queue, load_seen, record_auto_result
from .providers import limit_event, plain_limit_error
from .run_lock import application_lock

CONFIG_PATH = REPO / "web" / "queue_config.json"
RESULTS_DIR = REPO / "job_scraper" / "auto_results"

DEFAULT_CONFIG: dict[str, Any] = {
    "score_floor": 60,
    "per_job_budget_usd": 9,
    "evaluation_budget_usd": 3.0,
    "per_job_timeout_minutes": 25,
    "max_consecutive_failures": 3,
    "fill_forms_timeout_minutes": 75,
    "model": None,
}

ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "WebFetch",
        "WebSearch",
        "Agent",
        "Bash(cd:*)",
        "Bash(pdflatex:*)",
        "Bash(xelatex:*)",
        "Bash(lualatex:*)",
        "Bash(pdftotext:*)",
        "Bash(curl:*)",
        "Bash(bun run .agents/skills/*)",
        "Bash(python salary_lookup.py:*)",
    ]
)

_MAX_EVENTS = 2000
_STATE_LOCK = threading.RLock()
_state: dict[str, Any] = {
    "running": False,
    "stop_requested": False,
    "fill_forms": False,
    "started_at": None,
    "finished_at": None,
    "current": None,
    "results": [],
    "events": [],
    "next_id": 1,
    "message": "Idle.",
}
_proc: subprocess.Popen[str] | None = None


def claude_bin() -> str | None:
    """The `claude` executable. JOBDESK_CLAUDE_BIN overrides PATH (used by tests with a fake binary)."""
    return os.environ.get("JOBDESK_CLAUDE_BIN") or shutil.which("claude")


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return config


def _emit(message: str) -> None:
    with _STATE_LOCK:
        event = {"id": _state["next_id"], "ts": datetime.now().strftime("%H:%M:%S"), "message": message}
        _state["next_id"] += 1
        _state["events"].append(event)
        if len(_state["events"]) > _MAX_EVENTS:
            del _state["events"][: len(_state["events"]) - _MAX_EVENTS]


def status(since: int = 0) -> dict[str, Any]:
    with _STATE_LOCK:
        return {
            "running": _state["running"],
            "stop_requested": _state["stop_requested"],
            "fill_forms": _state["fill_forms"],
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
            "current": _state["current"],
            "results": list(_state["results"]),
            "message": _state["message"],
            "events": [e for e in _state["events"] if e["id"] > since],
            "last_id": _state["next_id"] - 1,
        }


def start(fill_forms: bool = False) -> dict[str, Any]:
    """Draft every queued job. With fill_forms, afterwards open each drafted job's employer form (still never submitting)."""
    with _STATE_LOCK:
        if _state["running"]:
            raise LaunchError("The queue is already running.", 409)
        if claude_bin() is None:
            raise LaunchError("The `claude` command was not found on PATH.", 500)
        if not list_queue():
            raise LaunchError("The queue is empty. Queue some jobs first.", 400)
        _state.update(
            running=True,
            stop_requested=False,
            fill_forms=fill_forms,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
            current=None,
            results=[],
            events=[],
            next_id=1,
            message="Starting.",
        )
    threading.Thread(target=_run, daemon=True, name="queue-runner").start()
    return status()


def stop() -> dict[str, Any]:
    with _STATE_LOCK:
        if not _state["running"]:
            return status()
        _state["stop_requested"] = True
        _state["message"] = "Stopping after killing the current job."
    _emit("Stop requested.")
    _kill_current()
    return status()


def _kill_current() -> None:
    proc = _proc
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.kill()


def _summarise(event: dict[str, Any]) -> str | None:
    """Turn one stream-json event into a short log line (or None to skip it)."""
    kind = event.get("type")
    if kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                name = block.get("name", "tool")
                arg = block.get("input") or {}
                detail = arg.get("command") or arg.get("file_path") or arg.get("url") or arg.get("query") or arg.get("description") or ""
                return f"{name}: {str(detail)[:110]}".strip()
            if block.get("type") == "text" and block.get("text", "").strip():
                return str(block["text"]).strip().splitlines()[0][:140]
    return None


def _cleanup_build_artifacts(since: float) -> None:
    for directory in (CV_DIR, COVER_DIR):
        # main_*.txt / cover_*.txt: text-layer extractions the model sometimes leaves beside the PDF (this run only, by mtime)
        for pattern in ("*.aux", "*.log", "*.out", "main_*.txt", "cover_*.txt"):
            for path in directory.glob(pattern):
                try:
                    if path.stat().st_mtime >= since - 5:
                        path.unlink()
                except OSError:
                    pass


def _read_result(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _provider_attempt(argv: list[str], deadline: float, emit: Callable[[str], None]) -> dict[str, Any]:
    global _proc
    if _state["stop_requested"]:
        return {"stopped": True}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"timed_out": True}
    timed_out = threading.Event()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = subprocess.Popen(
        argv, cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _proc = proc

    def on_timeout() -> None:
        timed_out.set()
        _kill_current()

    timer = threading.Timer(remaining, on_timeout)
    timer.daemon = True
    timer.start()
    info: dict[str, Any] = {"limited": False, "provider_error": False, "cost_usd": None}
    try:
        if _state["stop_requested"]:
            _kill_current()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if plain_limit_error(line):
                    info["limited"] = True
                    _kill_current()
                    break
                continue
            if not isinstance(event, dict):
                continue
            info["limited"] = info["limited"] or limit_event(event)
            kind = event.get("type")
            if kind in ("error", "turn.failed") or (kind == "result" and event.get("is_error")) or (kind == "assistant" and event.get("error")):
                info["provider_error"] = True
            if kind == "result":
                info["cost_usd"] = event.get("total_cost_usd")
            text = _summarise(event)
            if kind in ("item.started", "item.completed"):
                item = event.get("item") or {}
                text = item.get("command") or item.get("text")
            if text:
                emit(f"  {str(text).splitlines()[0][:140]}")
            if info["limited"]:
                _kill_current()
                break
        proc.wait()
    finally:
        timer.cancel()
        _proc = None
    info.update(returncode=proc.returncode, timed_out=timed_out.is_set(), stopped=_state["stop_requested"])
    return info


PHASES = ("auto", "evaluate", "draft")


def run_job(item: dict[str, str], config: dict[str, Any], emit: Callable[[str], None] | None = None,
            *, phase: str = "auto", run_id: str | None = None) -> dict[str, Any]:
    """phase "auto": evaluate and draft unattended (floor applies). "evaluate": Step 0-1 only, status "evaluated".
    "draft": the user approved the evaluation, so skip rules are off. Pass the same run_id to share one checkpoint."""
    if phase not in PHASES:
        raise ValueError(f"Unknown phase {phase!r}")
    with application_lock(validate_url(item["url"]), RESULTS_DIR):
        return _run_job(item, config, emit, phase=phase, run_id=run_id)


def _run_job(item: dict[str, str], config: dict[str, Any], emit: Callable[[str], None] | None = None,
             *, phase: str = "auto", run_id: str | None = None) -> dict[str, Any]:
    emit = emit or _emit
    url = validate_url(item["url"])
    run_id = run_id or f"{_slug(item.get('company', ''), item.get('title', ''))}_{uuid.uuid4().hex[:12]}"
    suffix = "" if phase == "auto" else f"_{phase}"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{run_id}{suffix}.json"
    progress_path = RESULTS_DIR / f"{run_id}_progress.json"
    result_rel = result_path.relative_to(REPO).as_posix()
    progress_rel = progress_path.relative_to(REPO).as_posix()
    arguments = f"{url} --auto --result {result_rel} --checkpoint {progress_rel}"
    if phase == "evaluate":
        arguments += " --evaluate-only"
    elif phase == "draft":
        arguments += " --approved"
    ok_statuses = ("evaluated",) if phase == "evaluate" else ("drafted", "skipped")
    total_budget = float(config.get("evaluation_budget_usd", 3.0)) if phase == "evaluate" else float(config["per_job_budget_usd"])
    argv = [
        claude_bin() or "claude", "-p", f"/apply {arguments}",
        "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS,
        "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", str(total_budget),
    ]
    if config.get("model"):
        argv += ["--model", str(config["model"])]
    protected_before = protected.snapshot()
    started = time.time()
    deadline = time.monotonic() + float(config["per_job_timeout_minutes"]) * 60
    outcome: dict[str, Any] = {"url": url, "company": item.get("company"), "title": item.get("title")}
    attempts: list[dict[str, Any]] = []
    log = emit

    try:
        log("Drafting with Claude")
        info = _provider_attempt(argv, deadline, log)
        attempts.append({"provider": "claude", **info})
        result = _read_result(result_path)
        tampered = protected.changed(protected_before)
        if tampered:
            backup = protected.backup_originals(protected_before, tampered, RESULTS_DIR / "protected_backup" / run_id)
            listing = "; ".join(tampered[:6]) + (f" (+{len(tampered) - 6} more)" if len(tampered) > 6 else "")
            outcome.update(
                status="failed",
                reason=f"Protected files changed during the run, so the result was discarded: {listing}. Originals saved in {backup.relative_to(REPO).as_posix()}. If you edited them yourself, ignore this and re-run.",
            )
        elif info.get("stopped"):
            outcome.update(status="stopped", reason="Stopped by user.")
        elif info.get("timed_out"):
            outcome.update(status="failed", reason=f"Timed out after {config['per_job_timeout_minutes']} minutes.")
        elif info.get("limited") and not (info.get("returncode") == 0 and not info.get("provider_error") and result and result.get("status") in ok_statuses):
            outcome.update(status="failed", reason="Claude reached its usage limit. Progress and any drafts are saved; run it again after the limit resets.")
        elif info.get("returncode") != 0 or info.get("provider_error"):
            outcome.update(status="failed", reason=f"Claude stopped with an error (exit code {info.get('returncode')}). Progress and any drafts are saved.")
        elif result is None or result.get("status") not in (*ok_statuses, "failed"):
            outcome.update(status="failed", reason="No valid result file written.")
        elif phase == "evaluate" and result["status"] == "evaluated":
            outcome.update(status="evaluated", evaluation=result.get("evaluation") or {}, reason=str((result.get("evaluation") or {}).get("recommendation") or ""))
        else:
            outcome.update(
                status=result["status"], score=result.get("score"), eligibility=result.get("eligibility"),
                reason=str(result.get("reason") or ""), warnings=result.get("warnings") or [],
                cv=result.get("cv"), cover=result.get("cover"),
            )
            record_auto_result(url, result)
        if (outcome.get("status") == "failed" and phase != "evaluate" and not tampered
                and not info.get("stopped")):
            # The model finished Steps 0-4 but could not compile (for example a sandbox blocked MiKTeX).
            # Compile and check the saved sources here, outside the sandbox. Never edits a source.
            # Deliberately tried even when info["timed_out"] is set: a sandbox-blocked MiKTeX call is
            # exactly the kind of thing that hangs and burns the whole per_job_timeout_minutes budget,
            # so excluding timeouts here used to skip rescue in the most common case that needs it.
            rescued = finalize.finalize(_read_result(progress_path), started)
            if rescued["ok"]:
                earlier = str(outcome.get("reason") or "the provider stopped before compiling").rstrip(". ")
                warnings = list(outcome.get("warnings") or []) + [(
                    f"Compiled and mechanically checked by the runner, not by a model, because: {earlier}. "
                    "Page counts, the text layer, date format and ruled-out phrases passed. The visual layout inspection, "
                    "keyword table and final checklist were NOT run by a model, so open both PDFs before sending."
                )]
                outcome.update(
                    status="drafted", cv=rescued["cv"], cover=rescued["cover"], score=rescued["score"],
                    warnings=warnings, reason="Drafted; finished by the runner after the model could not compile.",
                )
                record_auto_result(url, {"status": "drafted", "score": rescued["score"], "reason": outcome["reason"], "warnings": warnings})
                log("Compiled the saved drafts and ran the mechanical checks: both PDFs are ready.")
            else:
                outcome["warnings"] = list(outcome.get("warnings") or []) + [f"Could not finish from the saved sources: {rescued['reason']}"]
        outcome["attempts"] = attempts
        outcome["provider"] = "claude"
        cost = sum(a.get("cost_usd") or 0 for a in attempts)
        if cost:
            outcome["cost_usd"] = cost
        return outcome
    finally:
        _cleanup_build_artifacts(started)


def fill_argv(url: str, cv: Path, cover: Path) -> list[str]:
    """The exact command that fills one form: this job's own two PDFs, never a guess. No shell: one argv element per value."""
    return [sys.executable, str(REPO / "web" / "fill_form.py"), url, "--draft", "--cv", str(cv), "--cover", str(cover)]


def _form_attempt(argv: list[str], deadline: float, emit: Callable[[str], None]) -> dict[str, Any]:
    """Run one fill_form.py to the end. It returns when you finish with its Chrome window (or time runs out)."""
    global _proc
    if _state["stop_requested"]:
        return {"stopped": True}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"timed_out": True}
    timed_out = threading.Event()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # the filler calls Claude for free-text answers
    proc = subprocess.Popen(
        argv, cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _proc = proc

    def on_timeout() -> None:
        timed_out.set()
        _kill_current()

    timer = threading.Timer(remaining, on_timeout)
    timer.daemon = True
    timer.start()
    last_line = ""
    try:
        if _state["stop_requested"]:
            _kill_current()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if line:
                last_line = line
                emit(f"  {line[:160]}")
        proc.wait()
    finally:
        timer.cancel()
        _proc = None
    return {"returncode": proc.returncode, "timed_out": timed_out.is_set(), "stopped": _state["stop_requested"], "last_line": last_line}


def _fill_form(outcome: dict[str, Any], config: dict[str, Any], emit: Callable[[str], None]) -> dict[str, Any]:
    """Open one drafted job's employer form with its exact PDFs, fill it, and wait while you review. Never submits."""
    from apply_flow import checked_pdf  # imported here: apply_flow imports this module

    url = validate_url(outcome["url"])
    try:
        cv = checked_pdf(outcome.get("cv"), "cv")
        cover = checked_pdf(outcome.get("cover"), "cover_letters")
    except ValueError as err:
        return {"status": "failed", "reason": str(err)}
    minutes = float(config["fill_forms_timeout_minutes"])
    with application_lock(url, RESULTS_DIR):
        info = _form_attempt(fill_argv(url, cv, cover), time.monotonic() + minutes * 60, emit)
    if info.get("stopped"):
        return {"status": "stopped", "reason": "Stopped by user."}
    if info.get("timed_out"):
        return {"status": "failed", "reason": f"The form was still open after {minutes:g} minutes, so the run moved on and closed it."}
    code = info.get("returncode")
    if code == 0:
        return {"status": "filled", "reason": "Filled. Anything outlined in orange was left for you; the report is in the job's folder."}
    if code == 2:
        return {"status": "nothing_filled", "reason": "No form fields were found (a job description or sign-in wall). Open the job and use Fill form once you are past it."}
    return {"status": "failed", "reason": info.get("last_line") or f"The form filler exited with code {code}."}


def _fill_drafted_forms(config: dict[str, Any]) -> None:
    """After drafting: one form at a time, in queue order. The next opens when you finish with the previous window.
    Chrome uses a single profile, so forms cannot be filled in parallel. A form that fails never stops the others."""
    drafted = [r for r in _state["results"] if r.get("status") == "drafted"]
    if not drafted:
        _emit("Nothing was drafted, so there are no forms to fill.")
        return
    _emit(f"Opening {len(drafted)} form(s) one at a time. Review each in Chrome and submit it yourself; the next one opens when you close that window.")
    for outcome in drafted:
        if _state["stop_requested"]:
            break
        label = f"{outcome.get('company') or 'Unknown'} | {outcome.get('title') or outcome['url']}"
        with _STATE_LOCK:
            _state["current"] = {"company": outcome.get("company"), "title": outcome.get("title"), "url": outcome["url"]}
            _state["message"] = f"Form for {label}: review it in Chrome"
        _emit(f"Filling the form for {label}")
        try:
            form = _fill_form(outcome, config, _emit)
        except LaunchError as err:
            form = {"status": "failed", "reason": str(err)}
        except Exception as err:  # noqa: BLE001 - one bad form must not stop the rest
            form = {"status": "failed", "reason": f"{type(err).__name__}: {err}"}
        outcome["form"] = form
        _emit(f"Form for {label}: {form['status']}. {form.get('reason', '')}".rstrip())
        if form["status"] == "stopped":
            break
    filled = sum(1 for r in drafted if (r.get("form") or {}).get("status") == "filled")
    _emit(f"Forms: {filled} of {len(drafted)} filled.")


QUEUE_LOCK_KEY = "job-desk-queue-run"


def _run() -> None:
    """One queue run at a time across processes (the dashboard, `queue_cli.py`, a scheduler). Two runners would both take
    the first queued job, and one could dequeue a job the other was still drafting."""
    try:
        with application_lock(QUEUE_LOCK_KEY, RESULTS_DIR):
            _run_queue()
    except LaunchError:
        # _run_queue handles its own per-job errors, so a LaunchError reaching here means the lock is held elsewhere.
        message = "The queue is already running (in the dashboard or another command), so this run did nothing."
        _emit(message)
        with _STATE_LOCK:
            _state.update(running=False, current=None, message=message, finished_at=datetime.now().isoformat(timespec="seconds"))


def _run_queue() -> None:
    config = load_config()
    failures = 0
    try:
        while True:
            with _STATE_LOCK:
                if _state["stop_requested"]:
                    break
            queue_items = list_queue()
            if not queue_items:
                _state["message"] = "Queue finished."
                break
            item = queue_items[0]
            label = f"{item.get('company') or 'Unknown'} | {item.get('title') or item['url']}"
            with _STATE_LOCK:
                _state["current"] = {"company": item.get("company"), "title": item.get("title"), "url": item["url"]}
                _state["message"] = f"Working on {label}"
            _emit(f"Starting {label}")
            try:
                outcome = run_job(item, config)
            except LaunchError as err:
                outcome = {"url": item["url"], "company": item.get("company"), "title": item.get("title"), "status": "failed", "reason": str(err)}
            except Exception as err:  # noqa: BLE001 - one bad job must not kill the run
                outcome = {"url": item["url"], "company": item.get("company"), "title": item.get("title"), "status": "failed", "reason": f"{type(err).__name__}: {err}"}
            with _STATE_LOCK:
                _state["results"].append(outcome)
            limited = outcome["status"] == "failed" and "usage limit" in str(outcome.get("reason"))
            if outcome["status"] != "stopped" and not limited:  # a usage-limit failure keeps its place in the queue for the next run
                dequeue_job(item["url"])
            detail = outcome.get("reason") or ""
            score = f" (score {outcome['score']})" if outcome.get("score") is not None else ""
            _emit(f"Finished {label}: {outcome['status']}{score} {detail}".rstrip())
            failures = failures + 1 if outcome["status"] == "failed" else 0
            if limited:
                _state["message"] = "Stopped: Claude usage limit reached. The remaining jobs stay queued; run again after it resets."
                _emit(_state["message"])
                break
            if failures >= int(config["max_consecutive_failures"]):
                _state["message"] = f"Stopped after {failures} failures in a row."
                _emit(_state["message"])
                break
            if outcome["status"] == "stopped":
                break
        if _state["fill_forms"] and not _state["stop_requested"]:
            _fill_drafted_forms(config)
    finally:
        with _STATE_LOCK:
            _state["running"] = False
            _state["current"] = None
            _state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            if _state["stop_requested"]:
                _state["message"] = "Stopped."
        done = sum(1 for r in _state["results"] if r["status"] == "drafted")
        skipped = sum(1 for r in _state["results"] if r["status"] == "skipped")
        failed = sum(1 for r in _state["results"] if r["status"] == "failed")
        _emit(f"Run over: {done} drafted, {skipped} skipped, {failed} failed.")

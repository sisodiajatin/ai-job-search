"""Evaluate one job, show you the fit evaluation, and only on your yes draft the CV and letter and fill the form.

Never submits. Never drafts without your approval.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from api import queue_runner
from api.launcher import validate_url
from api.paths import REPO
from api.store import _slug, load_seen
from api.run_lock import application_lock

DIMENSIONS = (
    ("technical", "Technical skills"),
    ("experience", "Experience match"),
    ("behavioral", "Behavioral fit"),
    ("location", "Location"),
    ("career", "Career alignment"),
)


def checked_pdf(value: object, directory: str) -> Path:
    """Accept only an existing PDF in the expected repository directory."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"Drafting did not return a {directory} PDF.")
    path = (REPO / value).resolve()
    if not path.is_relative_to((REPO / directory).resolve()):
        raise ValueError(f"PDF must be inside {directory}.")
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError(f"Draft PDF is missing: {value}")
    return path


def format_evaluation(job: dict, evaluation: dict, floor: float) -> str:
    """Plain-text fit evaluation for the console (the same dimensions /apply Step 1 reports)."""
    lines = ["", "=" * 64, f"FIT EVALUATION: {job.get('title')} at {job.get('company')}", "=" * 64]
    score = evaluation.get("score")
    verdict = evaluation.get("verdict") or ""
    lines.append(f"Overall: {score}/100  {verdict}".rstrip())
    eligibility = str(evaluation.get("eligibility") or "unverified")
    lines.append(f"Eligibility gate: {eligibility.upper()}")
    if evaluation.get("eligibility_quote"):
        lines.append(f'  Posting says: "{evaluation["eligibility_quote"]}"')
    if eligibility == "fail":
        lines.append("  !! This posting appears to exclude you. Proceeding is your call.")
    elif eligibility == "unverified":
        lines.append("  The posting is silent on work authorization. Check the employer before relying on it.")
    dims = evaluation.get("dimensions") or {}
    lines.append("")
    for key, label in DIMENSIONS:
        d = dims.get(key) or {}
        if not d:
            continue
        value = d.get("result") if key == "location" else (f"{d.get('score')}/100" if d.get("score") is not None else "")
        note = f" - {d['note']}" if d.get("note") else ""
        lines.append(f"  {label:<18} {value}{note}")
    for title, key in (("Strengths", "strengths"), ("Gaps", "gaps")):
        items = evaluation.get(key) or []
        if items:
            lines += ["", f"{title}:"] + [f"  - {item}" for item in items]
    if evaluation.get("recommendation"):
        lines += ["", f"Recommendation: {evaluation['recommendation']}"]
    if isinstance(score, (int, float)) and score < floor:
        lines += ["", f"Note: this is below your usual score floor of {floor:g}."]
    lines.append("=" * 64)
    return "\n".join(lines)


def ask_proceed(prompt: str) -> bool:
    """Ask on the console. No terminal, end of input or anything but yes means no."""
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def run(url: str) -> int:
    url = validate_url(url)
    with application_lock(url, REPO / "job_scraper" / "auto_results"):
        return _run(url)


def _run(url: str) -> int:
    matches = [
        job for key, job in load_seen()["seen"].items()
        if isinstance(job, dict) and (key == url or job.get("url") == url)
    ]
    if len(matches) != 1:
        print("The listing must match exactly one saved job.", flush=True)
        return 1
    job = matches[0]
    item = {**job, "url": url}
    config = queue_runner.load_config()

    # The console runs in a separate process; its runner state is isolated from the server.
    def emit(message: str) -> None:
        print(message, flush=True)

    run_id = f"{_slug(str(job.get('company', '')), str(job.get('title', '')))}_{uuid.uuid4().hex[:12]}"
    print(f"Evaluating fit for {job.get('company')} | {job.get('title')} (no drafts yet)...", flush=True)
    evaluated = queue_runner.run_job(item, config, emit=emit, phase="evaluate", run_id=run_id)
    if evaluated.get("status") != "evaluated":
        print(f"Stopped before drafting: {evaluated.get('status')}: {evaluated.get('reason')}", flush=True)
        return 1
    print(format_evaluation(job, evaluated.get("evaluation") or {}, float(config.get("score_floor", 60))), flush=True)

    if not ask_proceed("\nDraft your CV and cover letter for this role, then open the employer's form? [y/N] "):
        print("Okay, nothing was drafted or opened. Run Apply now again if you change your mind.", flush=True)
        return 0

    print("Drafting CV and cover letter...", flush=True)
    result = queue_runner.run_job(item, config, emit=emit, phase="draft", run_id=run_id)
    if result.get("status") != "drafted":
        print(f"Browser filling stopped: {result.get('status')}: {result.get('reason')}", flush=True)
        return 0 if result.get("status") == "skipped" else 1
    cv = checked_pdf(result.get("cv"), "cv")
    cover = checked_pdf(result.get("cover"), "cover_letters")
    for warning in result.get("warnings") or []:
        print(f"NOTE: {warning}", flush=True)
    print("Both PDFs are ready. Opening the employer's form for filling and review...", flush=True)
    return subprocess.call([
        sys.executable, str(REPO / "web" / "fill_form.py"), url,
        "--draft", "--cv", str(cv), "--cover", str(cover),
    ], cwd=str(REPO))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    try:
        return run(args.url)
    except Exception as err:
        print(f"Application flow stopped: {err}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

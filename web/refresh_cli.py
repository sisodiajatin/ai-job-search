"""Daily refresh: scrape new listings, then have Claude rank the new ones with /rank, so auto_queue has fresh scored jobs to pick from.

Usage (from the repository root):
    python web/refresh_cli.py              # scrape + rank
    python web/refresh_cli.py --no-rank    # scrape only (free: no model usage)
    python web/refresh_cli.py --json       # also print a JSON summary as the last line

Ranking is the only part that uses model usage; it only looks at jobs with status "new" and never touches the tracker.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
RANK_TIMEOUT_SECONDS = 90 * 60
RANK_BUDGET_USD = "8"


RESULT_REL = "job_scraper/auto_results/rank_new.json"
PROMPT = (
    "/rank  -- unattended run, nobody will answer questions. Rank every job in job_scraper/seen_jobs.json with status \"new\" "
    "(skip the exclusion-set check; the tracker is irrelevant here). Follow Step 1-3 scoring exactly, but do NOT edit seen_jobs.json "
    "and do NOT present a shortlist. Instead write ONLY a JSON array to " + RESULT_REL + ", one object per job: "
    '{"key": <seen_jobs.json key>, "status": "scored"|"expired", "scores": {"technical":n,"experience":n,"behavioral":n,"career":n}, '
    '"location": "PASS"|"FLAG"|"FAIL", "language_gate": "PASS"|"FLAG"|"FAIL", "eligibility": "pass"|"fail"|"unverified", '
    '"eligibility_quote": string|null, "deadline": "YYYY-MM-DD"|null, "strengths": [..], "gaps": [..]}. '
    "Write the file as your last action, then reply with one short line."
)


def band(score: int) -> str:
    return "Strong Fit" if score >= 75 else "Good Fit" if score >= 60 else "Moderate Fit" if score >= 45 else "Weak Fit" if score >= 30 else "Poor Fit"


def apply_results(path: Path) -> int:
    """Copy the model's scores into seen_jobs.json (the model never edits that file). Returns how many jobs were updated."""
    from datetime import date
    from api.store import _lock, load_seen, save_seen

    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    done = 0
    with _lock:
        data = load_seen()
        seen = data["seen"]
        for row in rows if isinstance(rows, list) else []:
            job = seen.get(row.get("key")) if isinstance(row, dict) else None
            if not job or job.get("status") != "new":
                continue
            if row.get("status") == "expired" or not isinstance(row.get("scores"), dict):
                job["status"] = "expired"
                done += 1
                continue
            s = row["scores"]
            overall = round(.30 * s["technical"] + .25 * s["experience"] + .15 * s["behavioral"] + .30 * s["career"])
            job.update(status="ranked", rank_score=overall, rank_verdict=band(overall), rank_date=date.today().isoformat(),
                       location=row.get("location") or "PASS", language_gate=row.get("language_gate") or "PASS",
                       strengths=(row.get("strengths") or [])[:3], gaps=(row.get("gaps") or [])[:3],
                       eligibility={"fail": "FAIL", "pass": "PASS"}.get(row.get("eligibility"), "UNVERIFIED"))
            if row.get("eligibility_quote"):
                job["eligibility_note"] = row["eligibility_quote"]
            if row.get("deadline"):
                job["deadline"] = row["deadline"]
            done += 1
        save_seen(data)
    return done


def rank_new_jobs() -> tuple[bool, str]:
    from api.queue_runner import ALLOWED_TOOLS, REPO, claude_bin, load_config

    result = REPO / RESULT_REL
    result.unlink(missing_ok=True)
    argv = [claude_bin() or "claude", "-p", PROMPT, "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS,
            "--max-budget-usd", RANK_BUDGET_USD]
    model = load_config().get("model")
    if model:
        argv += ["--model", str(model)]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        subprocess.run(argv, cwd=str(REPO), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=RANK_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as err:
        return False, f"ranking did not finish: {err}"
    updated = apply_results(result)
    return updated > 0, f"{updated} job(s) scored"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-rank", action="store_true", help="scrape only, skip the model-based ranking")
    parser.add_argument("--json", action="store_true", help="print a JSON summary as the last line")
    args = parser.parse_args(argv)

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    from api.scrape import run_scrape

    errors: list[str] = []
    totals = run_scrape(lambda event: errors.append(event["message"]) if event.get("type") == "error" else None)
    summary = {"added": totals["added"], "existing": totals["existing"], "scrape_errors": totals["errors"], "ranked": None, "rank_note": ""}
    print(f"Scrape: {totals['added']} new, {totals['existing']} already known, {totals['errors']} error(s).", flush=True)
    for message in errors[:3]:
        print(f"  {message}", flush=True)
    from api.store import list_jobs

    unranked = sum(1 for job in list_jobs() if job.get("status") == "new" and not isinstance(job.get("rank_score"), (int, float)))
    summary["unranked"] = unranked
    if unranked and not args.no_rank:  # also catches yesterday's listings if its ranking run failed
        ok, note = rank_new_jobs()
        summary.update(ranked=ok, rank_note=note)
        print("Ranking: " + ("done." if ok else f"failed. {note}"), flush=True)
    if args.json:
        print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

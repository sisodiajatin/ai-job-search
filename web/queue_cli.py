"""Run the apply queue from the command line, for a scheduler or another agent (for example Hermes Agent).

Same code and the same limits as the dashboard's "Run queue" buttons. It never submits an application.

Usage (from the repository root):
    python web/queue_cli.py --status         # list the queue, run nothing
    python web/queue_cli.py                  # draft every queued job (CV + cover letter), unattended
    python web/queue_cli.py --fill-forms     # then open each drafted job's form in Chrome for you to review
    python web/queue_cli.py --json           # also print a machine-readable summary as the last line

Exit codes: 0 the run finished (read the summary: jobs can be skipped or failed), 1 it could not start
(empty queue, `claude` not on PATH), 3 the queue is already running elsewhere, 130 interrupted (Ctrl+C, the current job is stopped).

--fill-forms opens Chrome windows, so it must run in your logged-in desktop session. A background scheduler usually cannot show
windows there; schedule the drafting alone and fill the forms from the dashboard when you are at the desk.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
POLL_SECONDS = 1.0
BUSY_MARKER = "already running"


def summarise(results: list[dict]) -> dict:
    """What a scheduler needs to report back: counts and one line per job. No documents, no answers."""
    def one(r: dict) -> dict:
        form = r.get("form") or {}
        return {
            "company": r.get("company"), "title": r.get("title"), "status": r.get("status"), "score": r.get("score"),
            "reason": r.get("reason") or "", "form": form.get("status"),
        }
    jobs = [one(r) for r in results]
    count = lambda key, value: sum(1 for j in jobs if j[key] == value)  # noqa: E731
    return {
        "drafted": count("status", "drafted"), "skipped": count("status", "skipped"), "failed": count("status", "failed"),
        "forms_filled": count("form", "filled"), "jobs": jobs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fill-forms", action="store_true", help="after drafting, open each drafted job's employer form for review")
    parser.add_argument("--status", action="store_true", help="print the queue and exit")
    parser.add_argument("--json", action="store_true", help="print a JSON summary as the last line")
    args = parser.parse_args(argv)

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    from api import queue_runner
    from api.launcher import LaunchError
    from api.store import list_queue

    if args.status:
        queue = list_queue()
        print(f"{len(queue)} job(s) in the queue.")
        for item in queue:
            print(f"  {item.get('company') or 'Unknown'} | {item.get('title') or item['url']}")
        return 0

    try:
        queue_runner.start(fill_forms=args.fill_forms)
    except LaunchError as err:
        print(f"Could not start: {err}", flush=True)
        return 3 if err.status == 409 else 1

    last = 0
    try:
        while True:
            status = queue_runner.status(last)
            for event in status["events"]:
                print(f"[{event['ts']}] {event['message']}", flush=True)
            last = status["last_id"]
            if not status["running"]:
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Interrupted: stopping the current job.", flush=True)
        queue_runner.stop()
        while queue_runner.status()["running"]:
            time.sleep(0.2)
        return 130

    summary = summarise(status["results"])
    print(f"Done: {summary['drafted']} drafted, {summary['skipped']} skipped, {summary['failed']} failed"
          + (f", {summary['forms_filled']} form(s) filled." if args.fill_forms else "."), flush=True)
    if args.json:
        print(json.dumps(summary), flush=True)
    return 3 if BUSY_MARKER in str(status["message"]) and not status["results"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Put the best-fitting open jobs on the apply queue (high fit first, then medium). Queues only: it never drafts or submits.

Usage (from the repository root):
    python web/auto_queue.py --dry-run          # show what would be queued, change nothing
    python web/auto_queue.py                    # queue up to the per-run limit
    python web/auto_queue.py --limit 2 --min-fit high
    python web/auto_queue.py --json             # machine-readable summary as the last line

Limits live in web/queue_config.json under "auto_queue" (per_run_limit, max_queue_size, min_fit, retry_after_days).
Only jobs already rated by /rank are considered; unscored listings are never guessed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent


def format_result(result: dict) -> str:
    verb = "Would queue" if result["dry_run"] else "Queued"
    queued = result["queued"]
    if not queued:
        room = result["max_queue_size"] - result["queue_before"]
        why = "the queue is full" if room <= 0 else "no open job rated high or medium is waiting"
        return f"Nothing to queue: {why} ({result['queue_before']}/{result['max_queue_size']} in the queue)."
    lines = [f"{verb} {len(queued)} job(s), best fit first:"]
    lines += [f"  {j['fit']:<6} | {j.get('company') or 'Unknown'} | {j.get('title') or ''}" for j in queued]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show the picks, change nothing")
    parser.add_argument("--limit", type=int, help="override per_run_limit for this run")
    parser.add_argument("--min-fit", choices=("high", "medium"), help="override min_fit for this run")
    parser.add_argument("--json", action="store_true", help="print a JSON summary as the last line")
    args = parser.parse_args(argv)

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    from api import auto_queue, queue_runner

    config = queue_runner.load_config()
    overrides = {k: v for k, v in (("per_run_limit", args.limit), ("min_fit", args.min_fit)) if v is not None}
    config["auto_queue"] = {**auto_queue.settings(config), **overrides}

    result = auto_queue.auto_enqueue(config, dry_run=args.dry_run)
    print(format_result(result), flush=True)
    if args.json:
        print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

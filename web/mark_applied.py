"""Mark a listing as applied from the command line (same effect as the job desk's Submitted button).

Usage (from the repository root):
    python web/mark_applied.py "Netic"
    python web/mark_applied.py "https://www.linkedin.com/jobs/view/...-4438463495"

Writes the tracker row (status "applied"), sets application_status to "submitted" in
job_scraper/seen_jobs.json, and creates the archive folder with an outcome.md stub.
Safe to re-run: an existing tracker row is reused, never duplicated.
"""

from __future__ import annotations

import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent


def find_keys(query: str, seen: dict) -> list[str]:
    if query in seen:
        return [query]
    needle = query.strip().lower()
    return [
        key
        for key, rec in seen.items()
        if isinstance(rec, dict)
        and (needle in str(rec.get("company", "")).lower() or needle in key.lower())
    ]


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print(__doc__)
        return 2
    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    from api.store import load_seen, mark_applied

    keys = find_keys(sys.argv[1], load_seen()["seen"])
    if not keys:
        print(f"No listing matches {sys.argv[1]!r}.")
        return 1
    if len(keys) > 1:
        seen = load_seen()["seen"]
        print(f"{len(keys)} listings match {sys.argv[1]!r}; re-run with the exact URL:")
        for key in keys:
            rec = seen[key]
            print(f"  {rec.get('company')} | {rec.get('title')} | {key}")
        return 1

    result = mark_applied(keys[0])
    row = result["row"]
    verb = "Created" if result["created"] else "Already tracked, updated"
    print(f"{verb}: {row.get('company')} | {row.get('role')} | status={row.get('status')}")
    print(f"Archive folder: {row.get('folder') or result['job'].get('application_folder')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

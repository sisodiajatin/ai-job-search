"""Pick the listings most worth drafting and put them on the apply queue, best fit first.

This only *queues*. Drafting, form filling and everything after stay with the queue runner, which keeps its own skip rules
(failed eligibility gate, score below the floor). Nothing here drafts, fills or submits anything.

A job is a candidate when it is still open, was rated high or medium by /rank, and is not already queued, tracked, prepared or
submitted, did not fail the eligibility gate, was not skipped by an earlier automatic run, and was not tried automatically in the
last few days (so a transient failure is not retried in a loop).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .store import enqueue_job, list_jobs, list_queue

DEFAULTS: dict[str, Any] = {
    "enabled": False,       # only controls whether a scheduler (Hermes) does this on its own; the CLI always works
    "per_run_limit": 3,     # at most this many added per run: each draft costs real model usage
    "max_queue_size": 5,    # never let the queue grow past this
    "min_fit": "medium",    # "high" = only high; "medium" = high first, then medium
    "retry_after_days": 3,  # no automatic retry sooner than this after an automatic attempt
    "verified_eligibility_only": False,  # True: only jobs whose eligibility is already "pass" (saves an evaluation on roles that exclude F-1/OPT)
}
FIT_RANK = {"high": 0, "medium": 1}
OPEN_STATUSES = frozenset({"new", "ranked", "evaluated"})


def settings(config: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(config.get("auto_queue") or {})}


def _day(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def fit_of(job: dict[str, Any]) -> str:
    """The job's fit label, or one derived from its /rank score (75+ high, 60+ medium) when the label was never set."""
    label = str(job.get("fit") or "").lower()
    if label in FIT_RANK:
        return label
    score = job.get("rank_score")
    return "high" if isinstance(score, (int, float)) and score >= 75 else "medium" if isinstance(score, (int, float)) and score >= 60 else label


WELCOMING = re.compile(r"will sponsor|sponsorship available|\b(opt|f-1|cpt|h-1b)\b|visa not required", re.IGNORECASE)


NEGATED = re.compile(r"\b(no|not|cannot|unable|without)\b[^.]{0,40}\b(sponsor\w*|opt|f-1|visa)\b", re.IGNORECASE)


def sponsor_tier(job: dict[str, Any]) -> int:
    """0 = explicitly welcomes international candidates, 1 = unknown, 2 = fails the eligibility gate. # ponytail: keyword match on recorded notes, no H-1B data lookup."""
    if str(job.get("eligibility") or "").lower() == "fail":
        return 2
    text = f"{job.get('eligibility_note') or ''} {job.get('note') or ''}".lower()
    if NEGATED.search(text):  # "does not provide visa sponsorship or STEM OPT" mentions OPT but is a no
        return 2
    return 0 if str(job.get("eligibility") or "").lower() == "pass" or WELCOMING.search(text) else 1


def eligible(job: dict[str, Any], *, retry_after_days: int, today: date, verified_only: bool = False, skip_rules: bool = True) -> bool:
    if verified_only and str(job.get("eligibility") or "").lower() != "pass":
        return False
    if str(job.get("status")) not in OPEN_STATUSES:
        return False
    if fit_of(job) not in FIT_RANK:
        return False
    if job.get("queued") or job.get("tracker_status"):
        return False
    if str(job.get("application_status") or "").startswith(("prepared", "submitted")):
        return False
    if skip_rules and (str(job.get("eligibility") or "").lower() == "fail" or str(job.get("auto_status") or "") == "skipped"):
        return False
    last_try = _day(job.get("auto_run_at"))
    return not (last_try and (today - last_try).days < retry_after_days)


def pick(config: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """The jobs to queue now: high fit before medium, newest first within a fit level, capped by the per-run limit and queue room."""
    s = settings(config)
    today = today or date.today()
    worst = FIT_RANK.get(str(s["min_fit"]).lower(), FIT_RANK["medium"])
    limit = min(int(s["per_run_limit"]), max(0, int(s["max_queue_size"]) - len(list_queue())))
    if limit <= 0:
        return []
    candidates = [
        job for job in list_jobs()
        if eligible(job, retry_after_days=int(s["retry_after_days"]), today=today, verified_only=bool(s["verified_eligibility_only"]),
                        skip_rules=bool(config.get("skip_rules", True)))
        and FIT_RANK[fit_of(job)] <= worst
    ]
    candidates.sort(key=lambda job: str(job.get("first_seen") or ""), reverse=True)  # newest first...
    candidates.sort(key=lambda job: FIT_RANK[fit_of(job)])  # ...then high before medium...
    candidates.sort(key=sponsor_tier)  # ...then jobs that welcome F-1/OPT first, "no sponsorship" last (all stable)
    return candidates[:limit]


def auto_enqueue(config: dict[str, Any], *, dry_run: bool = False, today: date | None = None) -> dict[str, Any]:
    """Queue the picks (or just report them with dry_run). Returns what was or would be added."""
    s = settings(config)
    queue_before = len(list_queue())
    picked = pick(config, today)
    if not dry_run:
        for job in picked:
            enqueue_job(str(job["key"]))
    return {
        "dry_run": dry_run,
        "queue_before": queue_before,
        "max_queue_size": int(s["max_queue_size"]),
        "queued": [
            {"company": job.get("company"), "title": job.get("title"), "fit": fit_of(job), "first_seen": job.get("first_seen")}
            for job in picked
        ],
    }

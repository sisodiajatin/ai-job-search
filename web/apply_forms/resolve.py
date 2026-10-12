"""Find the employer's own application page for a listing, using the public job-board JSON APIs.

Only three platforms are supported (Ashby, Greenhouse, Lever). Slugs come from our own override
file or are guessed from the company name. URLs found inside a posting body are never used.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SLUGS_FILE = Path(__file__).with_name("slugs.json")
USER_AGENT = "job-desk/1.0 (personal job search; contact via GitHub sisodiajatin)"
TIMEOUT = 25


def _get_json(url: str) -> Any | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https hosts below
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def title_score(wanted: str, found: str) -> float:
    a, b = normalise(wanted), normalise(found)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb)
    if a in b or b in a:
        jaccard = max(jaccard, 0.9)
    return jaccard


def candidate_slugs(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    if not words:
        return []
    joined = "".join(words)
    options = [joined, "-".join(words), words[0]]
    for suffix in ("hq", "ai", "inc", "labs", "co"):
        options.append(joined + suffix)
    seen: list[str] = []
    for option in options:
        if option and option not in seen:
            seen.append(option)
    return seen


def load_overrides() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(SLUGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k.lower(): v for k, v in data.items() if isinstance(v, dict)}


def _ashby(slug: str) -> list[dict[str, Any]]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
    return [
        {"title": j.get("title", ""), "location": str(j.get("location") or ""), "job_url": j.get("jobUrl", ""), "apply_url": (j.get("applyUrl") or (j.get("jobUrl", "") + "/application")), "job_id": str(j.get("id", ""))}
        for j in jobs or []
        if j.get("jobUrl")
    ]


def _greenhouse(slug: str) -> list[dict[str, Any]]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
    return [
        {"title": j.get("title", ""), "location": str((j.get("location") or {}).get("name", "")), "job_url": j.get("absolute_url", ""), "apply_url": j.get("absolute_url", ""), "job_id": str(j.get("id", ""))}
        for j in jobs or []
        if j.get("absolute_url")
    ]


def _lever(slug: str) -> list[dict[str, Any]]:
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [
        {"title": j.get("text", ""), "location": str((j.get("categories") or {}).get("location", "")), "job_url": j.get("hostedUrl", ""), "apply_url": j.get("applyUrl") or (j.get("hostedUrl", "") + "/apply"), "job_id": str(j.get("id", ""))}
        for j in (data if isinstance(data, list) else [])
        if j.get("hostedUrl")
    ]


PLATFORMS = {"ashby": _ashby, "greenhouse": _greenhouse, "lever": _lever}


def resolve(company: str, title: str, min_score: float = 0.85) -> dict[str, Any]:
    """Return {"status": "found"|"ambiguous"|"not_found", ...}. Never raises on network failure."""
    overrides = load_overrides().get((company or "").lower(), {})
    attempts: list[tuple[str, str]] = [(p, s) for p, s in overrides.items() if p in PLATFORMS]
    for slug in candidate_slugs(company):
        for platform in PLATFORMS:
            if (platform, slug) not in attempts:
                attempts.append((platform, slug))

    for platform, slug in attempts:
        jobs = PLATFORMS[platform](slug)
        if not jobs:
            continue
        scored = sorted(((title_score(title, j["title"]), j) for j in jobs), key=lambda pair: pair[0], reverse=True)
        best_score = scored[0][0]
        if best_score < min_score:
            # The board exists but has no such role (closed?): do not keep guessing other platforms for it.
            return {"status": "not_found", "platform": platform, "slug": slug, "reason": f"Board found but no title close to {title!r} (best {best_score:.2f})."}
        top = [j for s, j in scored if s >= best_score - 1e-9]
        if len(top) > 1:
            return {"status": "ambiguous", "platform": platform, "slug": slug, "candidates": [{"title": j["title"], "location": j["location"], "apply_url": j["apply_url"]} for j in top]}
        job = top[0]
        return {"status": "found", "platform": platform, "slug": slug, "confidence": round(best_score, 2), **job}
    return {"status": "not_found", "reason": "No Ashby, Greenhouse or Lever board matched this company name. Add it to web/apply_forms/slugs.json."}

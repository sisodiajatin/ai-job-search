from __future__ import annotations

import csv
import json
import re
import shutil
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .paths import APPLICATIONS_DIR, APPLY_QUEUE, COVER_DIR, CV_DIR, REPO, SEEN_JOBS, TRACKER

_lock = threading.RLock()

TRACKER_FIELDS = [
    "date",
    "company",
    "sector",
    "role",
    "role_type",
    "channel",
    "status",
    "contact_person",
    "fit_rating",
    "notes",
    "cv_file",
    "cover_letter_file",
    "source",
]

# Board stages, in pipeline order. These are exactly the statuses /outcome and /gmail-sync write.
STAGES = ("applied", "interview", "offer", "hired", "rejected", "no_response", "withdrawn", "offer_declined")
TRACKER_STATUSES = frozenset(STAGES)
FINAL_TRACKER_STATUSES = frozenset({"hired", "rejected", "no_response", "withdrawn", "offer_declined"})
_STATUS_ALIASES = {"interviewing": "interview", "interviews": "interview", "noresponse": "no_response", "declined": "offer_declined"}


def normalize_status(raw: str | None) -> str:
    """Canonical stage for a free-text tracker status: "No response" -> no_response, "Offer Declined" -> offer_declined.
    Unknown values are returned lowercased so they can still be shown (as "other") and never dropped."""
    value = re.sub(r"[\s\-]+", "_", (raw or "").strip().lower())
    return _STATUS_ALIASES.get(value, value) or "applied"
FOLLOWUP_QUIET_DAYS = 10
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_FOLLOWED_UP_RE = re.compile(r"followed up (\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_OUTCOME_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(\S+)", re.MULTILINE)

_PORTAL_CHANNEL = {
    "linkedin-search": "linkedin",
    "freehire-search": "web",
    "websearch-fallback": "web",
}


def _empty_seen() -> dict[str, Any]:
    return {"seen": {}}


def _empty_queue() -> dict[str, Any]:
    return {"queue": []}


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _compact(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _rel(path: Path) -> str:
    return path.resolve().relative_to(REPO.resolve()).as_posix()


def _slug(company: str, role: str) -> str:
    def part(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")

    company_part = part(company)
    role_part = part(role)
    if company_part and role_part:
        return f"{company_part}_{role_part}"
    return company_part or role_part or "application"


def _parse_iso_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def quiet_stats(row: dict[str, str], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    notes = row.get("notes") or ""
    followups_sent = len(_FOLLOWED_UP_RE.findall(notes))
    found: list[date] = []
    applied = _parse_iso_date(row.get("date"))
    if applied:
        found.append(applied)
    for token in _DATE_RE.findall(notes):
        parsed = _parse_iso_date(token)
        if parsed:
            found.append(parsed)
    latest = max(found) if found else None
    days_quiet = (today - latest).days if latest else None
    status = normalize_status(row.get("status"))
    followup_due = (
        days_quiet is not None
        and days_quiet >= FOLLOWUP_QUIET_DAYS
        and followups_sent < 2
        and status not in FINAL_TRACKER_STATUSES
    )
    next_followup = None
    if latest and status not in FINAL_TRACKER_STATUSES and followups_sent < 2:
        next_followup = latest + timedelta(days=FOLLOWUP_QUIET_DAYS)
    return {
        "days_quiet": days_quiet,
        "followups_sent": followups_sent,
        "followup_due": followup_due,
        "last_contact": latest.isoformat() if latest else None,
        "next_followup": next_followup.isoformat() if next_followup else None,
        "followup_in_days": (next_followup - today).days if next_followup else None,
    }


def parse_outcome_status(folder: Path) -> str | None:
    path = folder / "outcome.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _OUTCOME_STATUS_RE.search(text)
    return match.group(1).strip() if match else None


def _archive_rel(job: dict[str, Any]) -> str:
    existing = str(job.get("application_folder") or "").replace("\\", "/").strip()
    if existing:
        return existing
    slug = _slug(str(job.get("company") or ""), str(job.get("title") or job.get("role") or ""))
    return f"documents/applications/{slug}"


def _best_tex_for(company: str, title: str, directory: Path, prefix: str) -> Path | None:
    best: tuple[int, Path] | None = None
    if not directory.is_dir():
        return None
    for item in directory.glob(f"{prefix}*.tex"):
        if item.name.lower().endswith("example.tex"):
            continue
        stem = item.stem.removeprefix(prefix)
        score = _stem_matches(stem, company, title)
        if score >= 2 and (best is None or score > best[0]):
            best = (score, item)
    return best[1] if best else None


def ensure_submit_archive(job: dict[str, Any], today: date | None = None) -> str:
    """Write an outcome stub and copy tex if missing. Never overwrite or fetch URLs."""
    today = today or date.today()
    rel = _archive_rel(job)
    folder = (REPO / rel.replace("\\", "/")).resolve()
    try:
        folder.relative_to(REPO.resolve())
    except ValueError:
        rel = f"documents/applications/{_slug(str(job.get('company') or ''), str(job.get('title') or ''))}"
        folder = (REPO / rel).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    company = str(job.get("company") or "")
    title = str(job.get("title") or job.get("role") or "")
    cv_tex = folder / "cv_draft.tex"
    if not cv_tex.is_file():
        source = _best_tex_for(company, title, CV_DIR, "main_")
        if source is not None:
            shutil.copy2(source, cv_tex)
    cover_tex = folder / "cover_letter.tex"
    if not cover_tex.is_file():
        source = _best_tex_for(company, title, COVER_DIR, "cover_")
        if source is not None:
            shutil.copy2(source, cover_tex)
    outcome = folder / "outcome.md"
    if not outcome.is_file():
        company_name = company or "Company"
        role = title or "Role"
        outcome.write_text(
            f"# Outcome: {company_name} — {role}\n\n"
            "**Status:** in_progress\n\n"
            "## Interview stages reached\n"
            "- [ ] Phone screen\n"
            "- [ ] Technical interview\n"
            "- [ ] Case interview\n"
            "- [ ] Final round\n"
            "- [ ] Offer received\n\n"
            "## Notes\n"
            f"{today.isoformat()} submitted\n",
            encoding="utf-8",
        )
    return Path(rel.replace("\\", "/")).as_posix()


def load_seen() -> dict[str, Any]:
    if not SEEN_JOBS.is_file():
        return _empty_seen()
    try:
        data = json.loads(SEEN_JOBS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_seen()
    if not isinstance(data, dict) or not isinstance(data.get("seen"), dict):
        return _empty_seen()
    return data


def save_seen(data: dict[str, Any]) -> None:
    SEEN_JOBS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_JOBS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(SEEN_JOBS)


def load_queue() -> list[dict[str, str]]:
    if not APPLY_QUEUE.is_file():
        return []
    try:
        data = json.loads(APPLY_QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("queue") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "url": url,
                "company": str(item.get("company") or ""),
                "title": str(item.get("title") or ""),
                "queued_at": str(item.get("queued_at") or ""),
            }
        )
    return out


def save_queue(items: list[dict[str, str]]) -> None:
    APPLY_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    tmp = APPLY_QUEUE.with_suffix(".json.tmp")
    payload = {"queue": items}
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(APPLY_QUEUE)


def load_tracker() -> list[dict[str, str]]:
    if not TRACKER.is_file():
        return []
    with TRACKER.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{field: (row.get(field) or "") for field in TRACKER_FIELDS} for row in reader]


def save_tracker(rows: list[dict[str, str]]) -> None:
    TRACKER.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRACKER.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) or "" for field in TRACKER_FIELDS})
    tmp.replace(TRACKER)


def _list_files(folder: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if not folder.is_dir():
        return files
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_file():
            continue
        files.append(
            {
                "name": item.name,
                "path": _rel(item),
                "kind": "pdf" if item.suffix.lower() == ".pdf" else "file",
            }
        )
    return files


def list_application_folders() -> list[dict[str, Any]]:
    folders: list[dict[str, Any]] = []
    if not APPLICATIONS_DIR.is_dir():
        return folders
    for item in sorted(APPLICATIONS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue
        folders.append(
            {
                "slug": item.name,
                "path": _rel(item),
                "files": _list_files(item),
                "outcome_status": parse_outcome_status(item),
            }
        )
    return folders


def list_draft_pdfs() -> list[dict[str, str]]:
    drafts: list[dict[str, str]] = []
    for directory, prefix in ((CV_DIR, "main_"), (COVER_DIR, "cover_")):
        if not directory.is_dir():
            continue
        for item in sorted(directory.glob(f"{prefix}*.pdf"), key=lambda p: p.name.lower()):
            if item.name.lower().endswith("example.pdf"):
                continue
            drafts.append({"name": item.name, "path": _rel(item), "kind": "pdf"})
    return drafts


def _folder_pdfs(folder_rel: str | None) -> tuple[str | None, str | None]:
    if not folder_rel:
        return None, None
    folder = (REPO / folder_rel.replace("\\", "/")).resolve()
    try:
        folder.relative_to(REPO.resolve())
    except ValueError:
        return None, None
    if not folder.is_dir():
        return None, None
    cv = None
    cover = None
    for item in folder.iterdir():
        if not item.is_file() or item.suffix.lower() != ".pdf":
            continue
        name = item.name.lower()
        if name in {"resume.pdf", "cv.pdf", "cv_draft.pdf"}:
            cv = _rel(item)
        elif name in {"cover_letter.pdf", "cover.pdf"}:
            cover = _rel(item)
    return cv, cover


def _stem_matches(stem: str, company: str, title: str) -> int:
    blob = _compact(stem)
    if not blob:
        return 0
    company_compact = _compact(company)
    score = 0
    if company_compact and company_compact in blob:
        score += 2
    tokens = [t for t in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(t) > 1]
    if tokens and all(t in blob for t in tokens):
        score += 1
    title_bits = [t for t in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(t) > 2]
    hits = sum(1 for t in title_bits if t in blob)
    if title_bits and hits:
        score += hits
    return score


def _draft_pdfs_for(company: str, title: str) -> tuple[str | None, str | None]:
    best_cv: tuple[int, str] | None = None
    best_cover: tuple[int, str] | None = None
    if CV_DIR.is_dir():
        for item in CV_DIR.glob("main_*.pdf"):
            if item.name.lower().endswith("example.pdf"):
                continue
            stem = item.stem.removeprefix("main_")
            score = _stem_matches(stem, company, title)
            if score >= 2 and (best_cv is None or score > best_cv[0]):
                best_cv = (score, _rel(item))
    if COVER_DIR.is_dir():
        for item in COVER_DIR.glob("cover_*.pdf"):
            if item.name.lower().endswith("example.pdf"):
                continue
            stem = item.stem.removeprefix("cover_")
            score = _stem_matches(stem, company, title)
            if score >= 2 and (best_cover is None or score > best_cover[0]):
                best_cover = (score, _rel(item))
    return (best_cv[1] if best_cv else None, best_cover[1] if best_cover else None)


def resolve_pdfs(job: dict[str, Any]) -> tuple[str | None, str | None]:
    folder = job.get("application_folder")
    cv, cover = _folder_pdfs(str(folder) if folder else None)
    if cv or cover:
        return cv, cover
    return _draft_pdfs_for(str(job.get("company") or ""), str(job.get("title") or ""))


def _tracker_match(row: dict[str, str], job: dict[str, Any]) -> bool:
    source = (row.get("source") or "").strip()
    url = (job.get("url") or job.get("key") or "").strip()
    if source and url and source == url:
        return True
    return _norm(row.get("company")) == _norm(str(job.get("company") or "")) and _norm(
        row.get("role")
    ) == _norm(str(job.get("title") or ""))


def _channel_for(portal: str | None) -> str:
    return _PORTAL_CHANNEL.get(portal or "", "web")


def _enrich_job(job: dict[str, Any], queue_urls: set[str], tracker: list[dict[str, str]]) -> dict[str, Any]:
    cv_pdf, cover_pdf = resolve_pdfs(job)
    url = str(job.get("url") or job.get("key") or "")
    tracker_status = ""
    for row in tracker:
        if _tracker_match(row, job):
            tracker_status = row.get("status") or ""
            if not cv_pdf:
                cv_pdf = row.get("cv_file") or None
            if not cover_pdf:
                cover_pdf = row.get("cover_letter_file") or None
            break
    job["cv_pdf"] = cv_pdf
    job["cover_pdf"] = cover_pdf
    job["queued"] = url in queue_urls
    job["tracker_status"] = tracker_status or None
    return job


def list_jobs() -> list[dict[str, Any]]:
    with _lock:
        seen = load_seen().get("seen", {})
        queue_urls = {item["url"] for item in load_queue()}
        tracker = load_tracker()
        jobs: list[dict[str, Any]] = []
        for key, record in seen.items():
            if not isinstance(record, dict):
                continue
            row = dict(record)
            row["key"] = key
            jobs.append(_enrich_job(row, queue_urls, tracker))
        return jobs


def update_job_status(key: str, status: str) -> dict[str, Any] | None:
    with _lock:
        data = load_seen()
        seen = data["seen"]
        if key not in seen or not isinstance(seen[key], dict):
            return None
        seen[key]["status"] = status
        save_seen(data)
        row = dict(seen[key])
        row["key"] = key
        queue_urls = {item["url"] for item in load_queue()}
        return _enrich_job(row, queue_urls, load_tracker())


def merge_listing(result: dict[str, Any], portal: str) -> str:
    """Insert a CLI result if the URL is new. Existing records are left untouched."""
    url = (result.get("url") or "").strip()
    if not url:
        return "skipped"
    with _lock:
        data = load_seen()
        seen: dict[str, Any] = data["seen"]
        if url in seen:
            return "existing"
        title = (result.get("title") or "").strip()
        company = (result.get("company") or "").strip()
        location = (result.get("location") or "").strip()
        posting_date = result.get("date") or result.get("posted") or None
        if isinstance(posting_date, str):
            posting_date = posting_date.strip() or None
        seen[url] = {
            "title": title,
            "company": company,
            "url": url,
            "location": location,
            "first_seen": date.today().isoformat(),
            "status": "new",
            "fit": "unscored",
            "portal": portal,
            "posting_date": posting_date,
        }
        save_seen(data)
        return "inserted"


def list_queue() -> list[dict[str, str]]:
    with _lock:
        return load_queue()


def enqueue_job(key: str) -> dict[str, Any]:
    with _lock:
        data = load_seen()
        seen = data["seen"]
        record = seen.get(key)
        if not isinstance(record, dict):
            url_match = next((k for k, v in seen.items() if isinstance(v, dict) and v.get("url") == key), None)
            if url_match:
                key = url_match
                record = seen[key]
        if not isinstance(record, dict):
            raise KeyError(key)
        url = str(record.get("url") or key).strip()
        items = load_queue()
        if any(item["url"] == url for item in items):
            return {"item": next(item for item in items if item["url"] == url), "created": False, "queue": items}
        item = {
            "url": url,
            "company": str(record.get("company") or ""),
            "title": str(record.get("title") or ""),
            "queued_at": date.today().isoformat(),
        }
        items.append(item)
        save_queue(items)
        return {"item": item, "created": True, "queue": items}


def dequeue_job(url: str) -> list[dict[str, str]]:
    with _lock:
        items = [item for item in load_queue() if item["url"] != url.strip()]
        save_queue(items)
        return items


def _enrich_tracker_row(row: dict[str, str], index: int, **extra: Any) -> dict[str, Any]:
    return {**row, "index": index, **quiet_stats(row), "stage": normalize_status(row.get("status")), **extra}


def _folder_matches_row(folder: dict[str, Any], row: dict[str, str]) -> bool:
    return _stem_matches(str(folder.get("slug") or ""), row.get("company") or "", row.get("role") or "") >= 2


def _tracker_folder(
    row: dict[str, str],
    folders: list[dict[str, Any]],
    seen: dict[str, Any],
) -> str | None:
    for key, record in seen.items():
        if not isinstance(record, dict):
            continue
        job = dict(record)
        job["key"] = key
        if _tracker_match(row, job):
            folder = str(record.get("application_folder") or "").replace("\\", "/").strip()
            if folder:
                return folder
    for folder in folders:
        if _folder_matches_row(folder, row):
            return str(folder.get("path") or "")
    return None


def mark_applied(key: str) -> dict[str, Any]:
    with _lock:
        data = load_seen()
        seen = data["seen"]
        record = seen.get(key)
        if not isinstance(record, dict):
            raise KeyError(key)
        job = dict(record)
        job["key"] = key
        cv_pdf, cover_pdf = resolve_pdfs(job)
        folder_rel = ensure_submit_archive(job)
        seen[key]["application_folder"] = folder_rel
        seen[key]["application_status"] = "submitted"
        tracker = load_tracker()
        for index, row in enumerate(tracker):
            if _tracker_match(row, job):
                save_seen(data)
                queue_urls = {item["url"] for item in load_queue()}
                job = dict(seen[key])
                job["key"] = key
                return {
                    "created": False,
                    "row": _enrich_tracker_row(row, index, folder=folder_rel),
                    "index": index,
                    "job": _enrich_job(job, queue_urls, tracker),
                }
        rank = job.get("rank_score")
        fit_rating = "" if rank is None else str(rank)
        row = {
            "date": date.today().isoformat(),
            "company": str(job.get("company") or ""),
            "sector": "",
            "role": str(job.get("title") or ""),
            "role_type": "",
            "channel": _channel_for(str(job.get("portal") or "")),
            "status": "applied",
            "contact_person": "",
            "fit_rating": fit_rating,
            "notes": "",
            "cv_file": cv_pdf or "",
            "cover_letter_file": cover_pdf or "",
            "source": str(job.get("url") or key),
        }
        tracker.append(row)
        save_tracker(tracker)
        save_seen(data)
        job = dict(seen[key])
        job["key"] = key
        queue_urls = {item["url"] for item in load_queue()}
        index = len(tracker) - 1
        return {
            "created": True,
            "row": _enrich_tracker_row(row, index, folder=folder_rel),
            "index": index,
            "job": _enrich_job(job, queue_urls, tracker),
        }


def note_apply_launched(key: str) -> None:
    """Stamp a listing when the dashboard opened a terminal for /apply (additive field only)."""
    with _lock:
        data = load_seen()
        record = data["seen"].get(key)
        if isinstance(record, dict):
            record["apply_launched_at"] = date.today().isoformat()
            save_seen(data)


def record_auto_result(url: str, result: dict[str, Any]) -> None:
    """Persist the outcome of an unattended `/apply --auto` run onto the listing (additive fields)."""
    with _lock:
        data = load_seen()
        seen = data["seen"]
        key = url if url in seen else next((k for k, v in seen.items() if isinstance(v, dict) and v.get("url") == url), None)
        if key is None or not isinstance(seen.get(key), dict):
            return
        record = seen[key]
        status = str(result.get("status") or "failed")
        record["auto_status"] = status
        record["auto_score"] = result.get("score")
        record["auto_reason"] = str(result.get("reason") or "")[:600]
        record["auto_warnings"] = [str(w)[:300] for w in (result.get("warnings") or [])][:10]
        record["auto_run_at"] = date.today().isoformat()
        if status == "drafted" and str(record.get("application_status") or "") != "submitted":
            record["application_status"] = "prepared"
            record["application_prepared_date"] = date.today().isoformat()
        save_seen(data)


def update_tracker_status(index: int, status: str) -> dict[str, Any]:
    with _lock:
        tracker = load_tracker()
        if index < 0 or index >= len(tracker):
            raise IndexError(index)
        tracker[index]["status"] = normalize_status(status)
        save_tracker(tracker)
        return _enrich_tracker_row(tracker[index], index)


def mark_followed_up(index: int, today: date | None = None) -> dict[str, Any]:
    """Record that a follow-up was sent today, using the `followed up YYYY-MM-DD` note /outcome already counts.
    Idempotent per day: a second click on the same day changes nothing."""
    day = (today or date.today()).isoformat()
    with _lock:
        tracker = load_tracker()
        if index < 0 or index >= len(tracker):
            raise IndexError(index)
        row = tracker[index]
        notes = row.get("notes") or ""
        marker = f"followed up {day}"
        if marker not in notes.lower():
            row["notes"] = f"{notes.rstrip()}; {marker}" if notes.strip() else marker
            save_tracker(tracker)
        return _enrich_tracker_row(row, index)


def today_summary(limit_picks: int = 5) -> dict[str, Any]:
    """What needs attention now: follow-ups due, drafts waiting to be sent, the queue, and a few fresh high-fit listings."""
    with _lock:
        pipeline = list_pipeline()
        followups = sorted(
            (row for row in pipeline["open"] if row.get("followup_due")),
            key=lambda row: row.get("days_quiet") or 0,
            reverse=True,
        )
        ready = [item for item in pipeline["prepared"] if item.get("key")]
        queued = load_queue()
        picks: list[dict[str, Any]] = []
        for job in list_jobs():
            if str(job.get("status")) != "new" or str(job.get("fit") or "").lower() != "high":
                continue
            if job.get("queued") or job.get("tracker_status"):
                continue
            if str(job.get("application_status") or "").startswith(("prepared", "submitted")):
                continue
            if str(job.get("eligibility") or "").lower() == "fail" or str(job.get("auto_status") or "") == "skipped":
                continue
            picks.append(job)
        picks.sort(key=lambda job: (job.get("rank_score") or 0, str(job.get("first_seen") or "")), reverse=True)
        picks = [
            {key: job.get(key) for key in ("key", "title", "company", "location", "url", "fit", "rank_score", "first_seen")}
            for job in picks[:limit_picks]
        ]
        return {
            "followups": followups,
            "ready": ready,
            "queued": queued,
            "picks": picks,
            "counts": {
                "followups": len(followups),
                "ready": len(ready),
                "queued": len(queued),
                "badge": len(followups) + len(ready),
            },
        }


def list_tracker() -> list[dict[str, Any]]:
    with _lock:
        return [_enrich_tracker_row(row, index) for index, row in enumerate(load_tracker())]


def list_pipeline() -> dict[str, Any]:
    with _lock:
        folders = list_application_folders()
        tracker = load_tracker()
        seen = load_seen().get("seen", {})
        open_rows: list[dict[str, Any]] = []
        matched_paths: set[str] = set()
        for index, row in enumerate(tracker):
            folder = _tracker_folder(row, folders, seen)
            if folder:
                matched_paths.add(folder.replace("\\", "/"))
            outcome_status = None
            if folder:
                outcome_status = parse_outcome_status(REPO / folder.replace("\\", "/"))
            open_rows.append(
                _enrich_tracker_row(row, index, folder=folder, outcome_status=outcome_status)
            )
        prepared: list[dict[str, Any]] = []
        for folder in folders:
            path = str(folder.get("path") or "").replace("\\", "/")
            if path in matched_paths:
                continue
            prepared.append({**folder, "kind": "folder"})
        for key, record in seen.items():
            if not isinstance(record, dict):
                continue
            status = str(record.get("application_status") or "")
            if not status.startswith("prepared"):
                continue
            job = dict(record)
            job["key"] = key
            if any(_tracker_match(row, job) for row in tracker):
                continue
            folder_rel = str(record.get("application_folder") or "").replace("\\", "/")
            if folder_rel and folder_rel in matched_paths:
                continue
            if folder_rel and any(str(item.get("path") or "").replace("\\", "/") == folder_rel for item in prepared):
                continue
            files = _list_files(REPO / folder_rel) if folder_rel and (REPO / folder_rel).is_dir() else []
            prepared.append(
                {
                    "slug": folder_rel.rsplit("/", 1)[-1] if folder_rel else key,
                    "path": folder_rel or None,
                    "files": files,
                    "company": record.get("company"),
                    "title": record.get("title"),
                    "outcome_status": parse_outcome_status(REPO / folder_rel) if folder_rel else None,
                    "kind": "listing",
                    "key": key,
                }
            )
        drafts = list_unlinked_drafts()
        return {
            "folders": folders,
            "drafts": drafts,
            "open": open_rows,
            "prepared": prepared,
            "followup_due": sum(1 for row in open_rows if row.get("followup_due")),
        }


def list_unlinked_drafts() -> list[dict[str, str]]:
    linked: set[str] = set()
    folders = list_application_folders()
    slugs = [folder["slug"] for folder in folders]
    for folder in folders:
        for file in folder["files"]:
            if file.get("kind") == "pdf":
                linked.add(file["path"])
    with _lock:
        seen = load_seen().get("seen", {})
        for record in seen.values():
            if not isinstance(record, dict):
                continue
            cv_pdf, cover_pdf = resolve_pdfs(record)
            if cv_pdf:
                linked.add(cv_pdf)
            if cover_pdf:
                linked.add(cover_pdf)
        for row in load_tracker():
            if row.get("cv_file"):
                linked.add(row["cv_file"].replace("\\", "/"))
            if row.get("cover_letter_file"):
                linked.add(row["cover_letter_file"].replace("\\", "/"))
    unlinked: list[dict[str, str]] = []
    for draft in list_draft_pdfs():
        if draft["path"] in linked:
            continue
        stem = _compact(Path(draft["name"]).stem)
        if any(_compact(slug) and _compact(slug) in stem for slug in slugs):
            continue
        unlinked.append(draft)
    return unlinked

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .files import resolve_allowed_file
from . import queue_runner
from .ext import build_plan, get_token, token_ok
from .launcher import LaunchError, launch_apply, launch_fill
from .paths import FRONTEND_DIST
from .scrape import discover_enabled_portals, load_scrape_config, planned_calls, run_scrape
from .store import (
    TRACKER_STATUSES,
    dequeue_job,
    enqueue_job,
    list_jobs,
    list_pipeline,
    list_queue,
    list_tracker,
    today_summary,
    mark_applied,
    mark_followed_up,
    normalize_status,
    note_apply_launched,
    update_job_status,
    update_tracker_status,
)

app = FastAPI(title="Job desk", docs_url=None, redoc_url=None)
ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

_scrape_lock = threading.Lock()

_DEV_HINT = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Job desk</title>
    <style>
      body { font: 16px/1.5 Georgia, serif; margin: 4rem auto; max-width: 36rem; color: #18202b; }
      code { font-family: ui-monospace, monospace; }
    </style>
  </head>
  <body>
    <p>The API is running. Build the UI or start the Vite dev server:</p>
    <p><code>cd web/frontend && npm install && npm run dev</code></p>
    <p>Or <code>npm run build</code> and refresh this page.</p>
  </body>
</html>
"""


def _guard(request: Request) -> None:
    """Refuse requests that did not come from this site. Used on endpoints that start processes."""
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Origin not allowed")
    if request.headers.get("x-job-desk") != "1":
        raise HTTPException(status_code=403, detail="Missing X-Job-Desk header")
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in {"127.0.0.1", "localhost"}:
        raise HTTPException(status_code=403, detail="Host not allowed")


class JobStatusPatch(BaseModel):
    key: str = Field(min_length=1)
    status: str = Field(min_length=1)


class QueueItem(BaseModel):
    key: str = Field(min_length=1)


class QueueRunBody(BaseModel):
    fill_forms: bool = False


class AppliedBody(BaseModel):
    key: str = Field(min_length=1)


class ApplyBody(BaseModel):
    key: str = Field(min_length=1)


class FollowupBody(BaseModel):
    index: int = Field(ge=0)


class TrackerStatusPatch(BaseModel):
    index: int = Field(ge=0)
    status: str = Field(min_length=1)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "portals": sorted(discover_enabled_portals())}


@app.get("/api/jobs")
def get_jobs() -> dict[str, Any]:
    jobs = list_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@app.patch("/api/jobs")
def patch_job(body: JobStatusPatch) -> dict[str, Any]:
    allowed = {"new", "skipped", "ranked", "expired", "evaluated"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed)}")
    row = update_job_status(body.key, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": row}


@app.get("/api/tracker")
def get_tracker() -> dict[str, Any]:
    rows = list_tracker()
    return {
        "rows": rows,
        "count": len(rows),
        "followup_due": sum(1 for row in rows if row.get("followup_due")),
    }


@app.get("/api/applications")
def get_applications() -> dict[str, Any]:
    return list_pipeline()


@app.get("/api/queue")
def get_queue() -> dict[str, Any]:
    items = list_queue()
    return {"queue": items, "count": len(items)}


@app.post("/api/queue")
def post_queue(body: QueueItem) -> dict[str, Any]:
    try:
        result = enqueue_job(body.key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    return result


@app.delete("/api/queue")
def delete_queue(url: str = Query(..., min_length=1)) -> dict[str, Any]:
    items = dequeue_job(url)
    return {"queue": items, "count": len(items)}


@app.post("/api/apply")
def post_apply(body: ApplyBody, request: Request) -> dict[str, Any]:
    """Start drafting followed by browser form filling. Guarded: starts a process."""
    _guard(request)
    jobs = {job["key"]: job for job in list_jobs()}
    job = jobs.get(body.key)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    url = str(job.get("url") or body.key)
    try:
        result = launch_apply(url)
    except LaunchError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from None
    dequeue_job(url)
    note_apply_launched(body.key)
    return {**result, "queue": list_queue()}


class ExtPlanBody(BaseModel):
    fields: list[dict[str, Any]] = Field(max_length=400)


@app.post("/api/ext/plan")
def post_ext_plan(body: ExtPlanBody, request: Request) -> dict[str, Any]:
    """Preview what the extension would fill. Needs the local token; never fills or submits."""
    _ext_auth(request)
    return build_plan(body.fields)


def _ext_auth(request: Request) -> None:
    origin = request.headers.get("origin") or ""
    if origin and not origin.startswith("chrome-extension://"):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    if not token_ok(request.headers.get("x-job-desk-token")):
        raise HTTPException(status_code=403, detail="Bad or missing token")


@app.get("/api/ext/drafts")
def get_ext_drafts(request: Request) -> list[dict[str, str]]:
    """Jobs that have a drafted CV, for the extension's picker."""
    _ext_auth(request)
    def age(job: dict[str, Any]) -> float:
        try:
            return resolve_allowed_file(job["cv_pdf"]).stat().st_mtime
        except (HTTPException, OSError):
            return 0.0

    newest = sorted((j for j in list_jobs() if j.get("cv_pdf")), key=age, reverse=True)[:40]
    return [{"key": j["key"], "label": f"{j.get('company', '')} - {j.get('title', '')}"} for j in newest]


@app.get("/api/ext/file")
def get_ext_file(request: Request, key: str, which: str = "cv") -> FileResponse:
    """One drafted PDF (cv or cover) for the extension to attach."""
    _ext_auth(request)
    job = {j["key"]: j for j in list_jobs()}.get(key)
    rel = job and job.get("cover_pdf" if which == "cover" else "cv_pdf")
    if not rel:
        raise HTTPException(status_code=404, detail="No such draft")
    return FileResponse(resolve_allowed_file(rel), media_type="application/pdf")


@app.get("/api/ext/token")
def get_ext_token(request: Request) -> dict[str, str]:
    """Token for pasting into the extension popup. Same-site dashboard only."""
    _guard(request)
    return {"token": get_token()}


@app.post("/api/fill")
def post_fill(body: ApplyBody, request: Request) -> dict[str, Any]:
    """Open Chrome on the employer's form and fill it (never submits). Guarded: starts a process."""
    _guard(request)
    job = {j["key"]: j for j in list_jobs()}.get(body.key)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("cv_pdf"):
        raise HTTPException(status_code=400, detail="Draft the CV and cover letter first (Apply now or Run queue).")
    try:
        return launch_fill(str(job.get("url") or body.key))
    except LaunchError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from None


@app.post("/api/queue/run")
def post_queue_run(request: Request, body: QueueRunBody | None = None) -> dict[str, Any]:
    """Start working through the apply queue unattended: drafts every job. With fill_forms, then opens each drafted
    job's employer form one at a time for you to review. Never submits."""
    _guard(request)
    try:
        return queue_runner.start(fill_forms=bool(body and body.fill_forms))
    except LaunchError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from None


@app.post("/api/queue/stop")
def post_queue_stop(request: Request) -> dict[str, Any]:
    _guard(request)
    return queue_runner.stop()


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return queue_runner.load_config()


class ConfigPatchBody(BaseModel):
    score_floor: float | None = None
    skip_rules: bool | None = None
    per_job_budget_usd: float | None = None
    evaluation_budget_usd: float | None = None
    per_job_timeout_minutes: int | None = None
    max_consecutive_failures: int | None = None
    auto_queue: dict[str, Any] | None = None


@app.patch("/api/config")
def patch_config(body: ConfigPatchBody, request: Request) -> dict[str, Any]:
    """Edit web/queue_config.json (score floor, eligibility gate, auto-queue caps). Guarded like other write endpoints."""
    _guard(request)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return queue_runner.save_config(patch)


@app.get("/api/queue/run")
def get_queue_run(since: int = 0) -> dict[str, Any]:
    return queue_runner.status(since)


@app.post("/api/applied")
def post_applied(body: AppliedBody) -> dict[str, Any]:
    try:
        result = mark_applied(body.key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    return result


@app.get("/api/today")
def get_today() -> dict[str, Any]:
    return today_summary()


@app.post("/api/tracker/followup")
def post_followup(body: FollowupBody, request: Request) -> dict[str, Any]:
    """Record "followed up <today>" on a tracker row. Guarded like the other endpoints that write personal data."""
    _guard(request)
    try:
        row = mark_followed_up(body.index)
    except IndexError:
        raise HTTPException(status_code=404, detail="Tracker row not found") from None
    return {"row": row, "index": body.index}


@app.patch("/api/tracker")
def patch_tracker(body: TrackerStatusPatch) -> dict[str, Any]:
    stage = normalize_status(body.status)
    if stage not in TRACKER_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(TRACKER_STATUSES)}")
    try:
        row = update_tracker_status(body.index, stage)
    except IndexError:
        raise HTTPException(status_code=404, detail="Tracker row not found") from None
    return {"row": row, "index": body.index}


@app.get("/api/files", response_model=None)
def get_file(path: str = Query(..., min_length=1)) -> FileResponse:
    full = resolve_allowed_file(path)
    suffix = full.suffix.lower()
    media = "application/octet-stream"
    if suffix == ".pdf":
        media = "application/pdf"
    elif suffix in {".txt", ".md"}:
        media = "text/plain; charset=utf-8"
    return FileResponse(full, media_type=media, filename=full.name)


@app.get("/api/scrape/plan")
def get_scrape_plan() -> dict[str, Any]:
    config = load_scrape_config()
    return {
        "jobage": config.get("jobage", 14),
        "limit": config.get("limit", 20),
        "portals": sorted(discover_enabled_portals()),
        "calls": planned_calls(),
    }


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _scrape_stream() -> Iterator[str]:
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def log(event: dict[str, Any]) -> None:
        events.put(event)

    def worker() -> None:
        try:
            run_scrape(log)
        except Exception as exc:  # noqa: BLE001 — surface in the UI log
            events.put({"type": "error", "message": str(exc)})
            events.put(
                {
                    "type": "done",
                    "added": 0,
                    "existing": 0,
                    "errors": 1,
                    "message": "Scrape failed.",
                }
            )
        finally:
            events.put(None)
            if _scrape_lock.locked():
                _scrape_lock.release()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    yield _sse({"type": "log", "message": "Starting scrape…"})
    while True:
        item = events.get()
        if item is None:
            break
        yield _sse(item)


@app.post("/api/scrape", response_model=None)
def post_scrape() -> StreamingResponse:
    if not _scrape_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scrape is already running")
    return StreamingResponse(
        _scrape_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", response_model=None)
def index() -> FileResponse | HTMLResponse:
    built = FRONTEND_DIST / "index.html"
    if built.is_file():
        return FileResponse(built, headers={"Cache-Control": "no-store"})
    return HTMLResponse(_DEV_HINT)


@app.get("/{full_path:path}", response_model=None)
def spa(full_path: str) -> FileResponse | HTMLResponse:
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="Not found")
    built = FRONTEND_DIST / "index.html"
    candidate = FRONTEND_DIST / full_path
    if candidate.is_file():
        return FileResponse(candidate)
    if built.is_file():
        return FileResponse(built, headers={"Cache-Control": "no-store"})
    return HTMLResponse(_DEV_HINT)

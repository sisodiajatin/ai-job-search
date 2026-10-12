from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1]
REPO = WEB_DIR.parent
SEEN_JOBS = REPO / "job_scraper" / "seen_jobs.json"
TRACKER = REPO / "job_search_tracker.csv"
APPLICATIONS_DIR = REPO / "documents" / "applications"
CV_DIR = REPO / "cv"
COVER_DIR = REPO / "cover_letters"
REPORTS_DIR = REPO / "reports"
APPLY_QUEUE = REPO / "job_scraper" / "apply_queue.json"
SCRAPE_CONFIG = WEB_DIR / "scrape_config.json"
AGENTS_SKILLS = REPO / ".agents" / "skills"
FRONTEND_DIST = WEB_DIR / "frontend" / "dist"

ALLOWED_FILE_PREFIXES = (
    "cv/",
    "cover_letters/",
    "documents/applications/",
    "reports/",
)

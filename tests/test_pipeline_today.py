"""Pipeline stages and the Today page: status normalisation, follow-up dates, guarded follow-up writes."""

import csv
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from fastapi.testclient import TestClient

from api import app as api_app
from api import store

HEADERS = {"X-Job-Desk": "1", "Origin": "http://127.0.0.1:8787"}


def tracker_row(company, status, when="2026-08-01", notes="", **extra):
    row = {field: "" for field in store.TRACKER_FIELDS}
    row.update(date=when, company=company, role="Engineer", status=status, notes=notes, fit_rating="77", source=f"https://example.com/{company}")
    row.update(extra)
    return row


class NormaliseTests(unittest.TestCase):
    def test_every_spelling_the_system_writes_maps_to_one_stage(self):
        table = {
            "applied": "applied", "Applied": "applied", "": "applied", None: "applied",
            "interview": "interview", "Interviewing": "interview",
            "offer": "offer", "hired": "hired", "HIRED": "hired",
            "rejected": "rejected",
            "no_response": "no_response", "no response": "no_response", "No-Response": "no_response", "No Response": "no_response",
            "withdrawn": "withdrawn",
            "offer_declined": "offer_declined", "offer declined": "offer_declined", "Offer Declined": "offer_declined",
        }
        for raw, expected in table.items():
            with self.subTest(raw=raw):
                self.assertEqual(store.normalize_status(raw), expected)

    def test_unknown_statuses_are_kept_not_dropped(self):
        self.assertEqual(store.normalize_status("Weird Status"), "weird_status")
        self.assertNotIn("weird_status", store.TRACKER_STATUSES)

    def test_board_stages_cover_everything_outcome_and_gmail_sync_write(self):
        for written in ("applied", "interview", "offer", "hired", "rejected", "no_response", "withdrawn", "offer_declined"):
            self.assertIn(written, store.TRACKER_STATUSES)


class QuietStatsTests(unittest.TestCase):
    today = date(2026, 10, 4)

    def stats(self, status, when="2026-09-20", notes=""):
        return store.quiet_stats(tracker_row("Acme", status, when, notes), today=self.today)

    def test_open_application_gets_a_follow_up_date_ten_days_after_last_contact(self):
        result = self.stats("applied", when="2026-10-01")
        self.assertEqual(result["last_contact"], "2026-10-01")
        self.assertEqual(result["next_followup"], "2026-10-11")
        self.assertEqual(result["followup_in_days"], 7)
        self.assertFalse(result["followup_due"])

    def test_quiet_application_is_due_now(self):
        result = self.stats("applied", when="2026-09-20")
        self.assertTrue(result["followup_due"])
        self.assertEqual(result["days_quiet"], 14)
        self.assertLess(result["followup_in_days"], 0)

    def test_latest_dated_note_counts_as_contact(self):
        result = self.stats("interview", when="2026-09-01", notes="phone screen 2026-10-02")
        self.assertEqual(result["last_contact"], "2026-10-02")
        self.assertFalse(result["followup_due"])

    def test_final_statuses_never_nag_whatever_the_spelling(self):
        for status in ("rejected", "hired", "no response", "No_Response", "withdrawn", "offer declined", "Offer Declined"):
            with self.subTest(status=status):
                result = self.stats(status, when="2026-06-01")
                self.assertFalse(result["followup_due"])
                self.assertIsNone(result["next_followup"])

    def test_offer_stage_is_still_open_for_follow_up(self):
        self.assertTrue(self.stats("offer", when="2026-09-01")["followup_due"])

    def test_two_follow_ups_sent_stops_the_reminders(self):
        result = self.stats("applied", when="2026-08-01", notes="followed up 2026-08-15; followed up 2026-09-01")
        self.assertEqual(result["followups_sent"], 2)
        self.assertFalse(result["followup_due"])
        self.assertIsNone(result["next_followup"])


class StoreAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "job_scraper").mkdir()
        (self.root / "documents" / "applications").mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("REPO", self.root),
            ("SEEN_JOBS", self.root / "job_scraper" / "seen_jobs.json"),
            ("APPLY_QUEUE", self.root / "job_scraper" / "apply_queue.json"),
            ("TRACKER", self.root / "tracker.csv"),
            ("APPLICATIONS_DIR", self.root / "documents" / "applications"),
            ("CV_DIR", self.root / "cv"),
            ("COVER_DIR", self.root / "cover_letters"),
        ):
            self.stack.enter_context(patch.object(store, name, value))
        self.client = TestClient(api_app.app, base_url="http://127.0.0.1:8787")

    def write_tracker(self, rows):
        store.save_tracker(rows)

    def write_seen(self, seen):
        (self.root / "job_scraper" / "seen_jobs.json").write_text(json.dumps({"seen": seen}), encoding="utf-8")

    def write_queue(self, items):
        (self.root / "job_scraper" / "apply_queue.json").write_text(json.dumps({"queue": items}), encoding="utf-8")

    def test_cards_with_every_written_status_get_a_stage_so_none_are_invisible(self):
        self.write_tracker([
            tracker_row("A", "applied"), tracker_row("B", "Offer"), tracker_row("C", "hired"),
            tracker_row("D", "no response"), tracker_row("E", "Offer declined"), tracker_row("F", "mystery"),
        ])
        rows = {row["company"]: row["stage"] for row in store.list_pipeline()["open"]}
        self.assertEqual(rows, {"A": "applied", "B": "offer", "C": "hired", "D": "no_response", "E": "offer_declined", "F": "mystery"})

    def test_patch_accepts_new_stages_in_any_spelling_and_stores_the_canonical_one(self):
        self.write_tracker([tracker_row("A", "applied")])
        for sent, saved in (("offer", "offer"), ("Offer Declined", "offer_declined"), ("no response", "no_response"), ("hired", "hired")):
            with self.subTest(sent=sent):
                response = self.client.patch("/api/tracker", json={"index": 0, "status": sent})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(store.load_tracker()[0]["status"], saved)
                self.assertEqual(response.json()["row"]["stage"], saved)

    def test_patch_rejects_junk_and_missing_rows(self):
        self.write_tracker([tracker_row("A", "applied")])
        self.assertEqual(self.client.patch("/api/tracker", json={"index": 0, "status": "banana"}).status_code, 400)
        self.assertEqual(self.client.patch("/api/tracker", json={"index": 9, "status": "offer"}).status_code, 404)
        self.assertEqual(store.load_tracker()[0]["status"], "applied")

    def test_followed_up_is_recorded_once_per_day_and_counts_toward_the_limit(self):
        self.write_tracker([tracker_row("A", "applied", notes="screen booked")])
        day = date(2026, 10, 4)
        first = store.mark_followed_up(0, today=day)
        again = store.mark_followed_up(0, today=day)
        notes = store.load_tracker()[0]["notes"]
        self.assertEqual(notes, "screen booked; followed up 2026-10-04")
        self.assertEqual(notes.count("followed up"), 1)
        self.assertEqual((first["followups_sent"], again["followups_sent"]), (1, 1))
        store.mark_followed_up(0, today=date(2026, 10, 15))
        self.assertEqual(store.load_tracker()[0]["notes"].count("followed up"), 2)

    def test_followed_up_endpoint_is_guarded(self):
        self.write_tracker([tracker_row("A", "applied")])
        body = {"index": 0}
        self.assertEqual(self.client.post("/api/tracker/followup", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/tracker/followup", json=body, headers={"X-Job-Desk": "1", "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/tracker/followup", json=body, headers={**HEADERS, "Host": "evil.example:8787"}).status_code, 403)
        self.assertEqual(store.load_tracker()[0]["notes"], "", "blocked requests must not write")
        self.assertEqual(self.client.post("/api/tracker/followup", json={"index": 7}, headers=HEADERS).status_code, 404)
        ok = self.client.post("/api/tracker/followup", json=body, headers=HEADERS)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("followed up", store.load_tracker()[0]["notes"])

    def test_today_buckets(self):
        self.write_tracker([
            tracker_row("Quiet", "applied", when="2026-08-01"),
            tracker_row("Fresh", "applied", when=date.today().isoformat()),
            tracker_row("Closed", "no response", when="2026-06-01"),
            tracker_row("Tracked", "applied", when=date.today().isoformat(), source="https://example.com/tracked"),
        ])
        def job(url, company, **extra):
            return {url: {"company": company, "title": "Engineer", "url": url, "fit": "high", "status": "new", "first_seen": "2026-10-03", "rank_score": 80, **extra}}
        seen = {}
        seen.update(job("https://example.com/pick-a", "PickA", rank_score=90))
        seen.update(job("https://example.com/pick-b", "PickB", rank_score=70))
        seen.update(job("https://example.com/queued", "Queued"))
        seen.update(job("https://example.com/tracked", "Tracked"))
        seen.update(job("https://example.com/low", "Low", fit="low"))
        seen.update(job("https://example.com/skipped", "Skipped", auto_status="skipped"))
        seen.update(job("https://example.com/failed", "Failed", eligibility="FAIL"))
        seen.update(job("https://example.com/prepared", "Prepared", application_status="prepared"))
        self.write_seen(seen)
        self.write_queue([{"url": "https://example.com/queued", "company": "Queued", "title": "Engineer", "queued_at": "2026-10-04"}])

        today = self.client.get("/api/today").json()
        self.assertEqual([row["company"] for row in today["followups"]], ["Quiet"])
        self.assertEqual([item["company"] for item in today["ready"]], ["Prepared"])
        self.assertEqual([item["company"] for item in today["queued"]], ["Queued"])
        self.assertEqual([pick["company"] for pick in today["picks"]], ["PickA", "PickB"], "best first; excludes queued, tracked, low fit, skipped, ineligible and prepared")
        self.assertEqual(today["counts"], {"followups": 1, "ready": 1, "queued": 1, "badge": 2})

    def test_today_is_empty_and_valid_with_no_data(self):
        today = self.client.get("/api/today").json()
        self.assertEqual(today["counts"], {"followups": 0, "ready": 0, "queued": 0, "badge": 0})
        self.assertEqual((today["followups"], today["ready"], today["queued"], today["picks"]), ([], [], [], []))


if __name__ == "__main__":
    unittest.main()

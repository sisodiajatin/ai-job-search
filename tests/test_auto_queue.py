"""Auto-queue: which jobs get picked (high before medium, newest first), what is never picked, and the caps."""

import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import auto_queue as auto_queue_cli
from api import auto_queue, queue_runner

TODAY = date(2026, 10, 7)


def job(key, fit="high", status="ranked", first_seen="2026-10-01", **extra):
    return {"key": key, "url": key, "company": f"Co {key}", "title": "Engineer", "status": status, "fit": fit,
            "first_seen": first_seen, "queued": False, "tracker_status": None, **extra}


class PickTests(unittest.TestCase):
    def pick(self, jobs, queue=(), **config):
        with patch.object(auto_queue, "list_jobs", return_value=jobs), patch.object(auto_queue, "list_queue", return_value=list(queue)):
            return [j["key"] for j in auto_queue.pick({"auto_queue": config}, TODAY)]

    def test_visa_welcoming_jobs_first_and_no_sponsorship_last(self):
        tier = auto_queue.sponsor_tier
        self.assertEqual(tier(job("a", eligibility_note="Will sponsor")), 0)
        self.assertEqual(tier(job("b", note="OPT welcome")), 0)
        self.assertEqual(tier(job("c", note="optional extras")), 1)  # "optional" is not OPT
        self.assertEqual(tier(job("d", eligibility="fail")), 2)
        self.assertEqual(tier(job("e", eligibility_note="does not provide visa sponsorship or STEM OPT extensions.")), 2)

    def test_high_before_medium_then_newest_first(self):
        jobs = [
            job("m-new", "medium", first_seen="2026-10-06"),
            job("h-old", "high", first_seen="2026-09-20"),
            job("h-new", "high", first_seen="2026-10-05"),
            job("m-old", "medium", first_seen="2026-09-25"),
        ]
        self.assertEqual(self.pick(jobs, per_run_limit=10), ["h-new", "h-old", "m-new", "m-old"])

    def test_min_fit_high_excludes_medium(self):
        self.assertEqual(self.pick([job("h", "high"), job("m", "medium")], min_fit="high", per_run_limit=10), ["h"])

    def test_never_picks_unscored_low_or_closed_jobs(self):
        jobs = [job("a", "unscored", "new"), job("b", "low"), job("c", status="expired"), job("d", status="skipped"), job("e", status="submitted"), job("ok")]
        self.assertEqual(self.pick(jobs, per_run_limit=10), ["ok"])

    def test_skips_queued_tracked_prepared_submitted_and_failed_eligibility(self):
        jobs = [
            job("queued", queued=True), job("tracked", tracker_status="applied"), job("prepared", application_status="prepared"),
            job("done", application_status="submitted"), job("gate", eligibility="fail"), job("auto-skipped", auto_status="skipped"),
            job("unverified", eligibility="unverified"), job("good", eligibility="pass"),
        ]
        self.assertEqual(self.pick(jobs, per_run_limit=10), ["good", "unverified"])  # unverified is allowed (the runner's gate decides), just after jobs that welcome visas

    def test_verified_eligibility_only_keeps_just_the_jobs_already_cleared(self):
        jobs = [job("pass", eligibility="pass"), job("unverified", eligibility="unverified"), job("none")]
        self.assertEqual(self.pick(jobs, per_run_limit=10), ["pass", "unverified", "none"])  # default: the runner's gate decides later
        self.assertEqual(self.pick(jobs, per_run_limit=10, verified_eligibility_only=True), ["pass"])

    def test_no_automatic_retry_within_the_cool_off(self):
        jobs = [job("recent", auto_status="failed", auto_run_at="2026-10-06"), job("old", auto_status="failed", auto_run_at="2026-10-01")]
        self.assertEqual(self.pick(jobs, per_run_limit=10, retry_after_days=3), ["old"])

    def test_per_run_limit_and_queue_room_both_cap(self):
        jobs = [job(str(i)) for i in range(8)]
        self.assertEqual(len(self.pick(jobs, per_run_limit=3, max_queue_size=10)), 3)
        self.assertEqual(len(self.pick(jobs, queue=[1, 2, 3, 4], per_run_limit=3, max_queue_size=5)), 1)  # one slot left
        self.assertEqual(self.pick(jobs, queue=[1, 2, 3, 4, 5], per_run_limit=3, max_queue_size=5), [])  # full


class AutoEnqueueTests(unittest.TestCase):
    JOBS = [job("a"), job("b", "medium")]

    def run_it(self, **kwargs):
        with patch.object(auto_queue, "list_jobs", return_value=self.JOBS), patch.object(auto_queue, "list_queue", return_value=[]), \
             patch.object(auto_queue, "enqueue_job") as enqueue:
            result = auto_queue.auto_enqueue({"auto_queue": {"per_run_limit": 5}}, today=TODAY, **kwargs)
        return result, enqueue

    def test_queues_the_picks_in_order(self):
        result, enqueue = self.run_it()
        self.assertEqual([c.args[0] for c in enqueue.call_args_list], ["a", "b"])
        self.assertEqual([q["fit"] for q in result["queued"]], ["high", "medium"])

    def test_dry_run_changes_nothing(self):
        result, enqueue = self.run_it(dry_run=True)
        enqueue.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["queued"]), 2)


class CliTests(unittest.TestCase):
    def run_cli(self, argv, picked=()):
        result = {"dry_run": "--dry-run" in argv, "queue_before": 1, "max_queue_size": 5, "queued": list(picked)}
        out = io.StringIO()
        with patch.object(auto_queue, "auto_enqueue", return_value=result) as run, patch.object(queue_runner, "load_config", return_value=dict(queue_runner.DEFAULT_CONFIG)), \
             contextlib.redirect_stdout(out):
            code = auto_queue_cli.main(argv)
        return code, out.getvalue(), run

    def test_prints_the_picks_and_a_json_summary(self):
        picked = [{"company": "Acme", "title": "Dev", "fit": "high", "first_seen": "2026-10-05"}]
        code, text, _ = self.run_cli(["--json"], picked)
        self.assertEqual(code, 0)
        self.assertIn("Queued 1 job(s), best fit first:", text)
        self.assertIn("high   | Acme | Dev", text)
        self.assertEqual(json.loads(text.strip().splitlines()[-1])["queued"][0]["company"], "Acme")

    def test_dry_run_wording_and_overrides_reach_the_picker(self):
        code, text, run = self.run_cli(["--dry-run", "--limit", "2", "--min-fit", "high"], [{"company": "A", "title": "T", "fit": "high"}])
        self.assertIn("Would queue 1 job(s)", text)
        self.assertTrue(run.call_args.kwargs["dry_run"])
        self.assertEqual(run.call_args.args[0]["auto_queue"]["per_run_limit"], 2)
        self.assertEqual(run.call_args.args[0]["auto_queue"]["min_fit"], "high")

    def test_explains_why_nothing_was_queued(self):
        self.assertIn("no open job rated high or medium", self.run_cli([])[1])


if __name__ == "__main__":
    unittest.main()

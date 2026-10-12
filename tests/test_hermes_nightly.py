"""The nightly Hermes job: a fixed command, quiet when there is nothing to say, never raw logs, never --fill-forms."""

import importlib.util
import io
import contextlib
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "web" / "hermes_job" / "nightly_queue.py"
spec = importlib.util.spec_from_file_location("nightly_queue", SCRIPT)
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)

SUMMARY = {
    "drafted": 1, "skipped": 1, "failed": 0, "forms_filled": 0,
    "jobs": [
        {"company": "Acme", "title": "Engineer", "status": "drafted", "score": 82, "reason": "", "form": None},
        {"company": "Beta", "title": "Dev", "status": "skipped", "score": 41, "reason": "below the floor\nof 60", "form": None},
    ],
}


def run_main(returncode=0, stdout="", raises=None):
    out = io.StringIO()
    completed = SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    with patch.object(nightly, "auto_queue_enabled", return_value=False), patch.object(nightly, "refresh_enabled", return_value=False), patch.object(nightly, "approval_enabled", return_value=False), \
         patch.object(nightly.subprocess, "run", side_effect=raises, return_value=completed) as run, contextlib.redirect_stdout(out):
        code = nightly.main()
    return code, out.getvalue(), run


class NightlyQueueTests(unittest.TestCase):
    def test_runs_exactly_one_fixed_command_without_fill_forms(self):
        _, _, run = run_main(stdout="Done.\n" + json.dumps(SUMMARY))
        argv = run.call_args.args[0]
        self.assertEqual(len(argv), 3)
        self.assertTrue(argv[1].endswith("queue_cli.py"))
        self.assertEqual(argv[2], "--json")
        self.assertNotIn("--fill-forms", argv)
        self.assertIs(run.call_args.kwargs.get("shell", False), False)

    def test_prints_a_short_summary_and_never_the_raw_log(self):
        log = "[10:00:00] Drafting with Claude\n  Write: cv/main_acme.tex\n"
        code, text, _ = run_main(stdout=log + json.dumps(SUMMARY))
        self.assertEqual(code, 0)
        self.assertIn("1 drafted, 1 skipped, 0 failed", text)
        self.assertIn("Acme | Engineer: drafted (82)", text)
        self.assertIn("Beta | Dev: skipped (41) - below the floor of 60", text)  # newline in the reason is flattened
        self.assertNotIn("Drafting with Claude", text)
        self.assertNotIn("main_acme.tex", text)

    def test_an_empty_queue_stays_silent(self):
        code, text, _ = run_main(returncode=1, stdout="Could not start: The queue is empty. Queue some jobs first.")
        self.assertEqual((code, text), (0, ""))

    def test_a_busy_queue_is_reported_but_is_not_a_failure(self):
        code, text, _ = run_main(returncode=3, stdout="Could not start: The queue is already running.")
        self.assertEqual(code, 0)
        self.assertIn("already running", text)

    def test_real_problems_raise_a_failure(self):
        code, text, _ = run_main(returncode=1, stdout="Could not start: The `claude` command was not found on PATH.")
        self.assertEqual(code, 1)
        self.assertIn("claude", text)
        self.assertEqual(run_main(returncode=2, stdout="boom")[0], 1)
        self.assertEqual(run_main(returncode=0, stdout="no json here")[0], 1)

    def test_a_hang_is_stopped_and_reported(self):
        code, text, _ = run_main(raises=subprocess.TimeoutExpired("x", 1))
        self.assertEqual(code, 1)
        self.assertIn("12 hours", text)

    def test_passes_claudes_location_so_a_scheduler_with_a_short_path_still_works(self):
        with patch.object(nightly, "find_claude", return_value=r"C:\Users\x\.local\bin\claude.exe"):
            _, _, run = run_main(stdout=json.dumps(SUMMARY))
        self.assertEqual(run.call_args.kwargs["env"]["JOBDESK_CLAUDE_BIN"], r"C:\Users\x\.local\bin\claude.exe")


AUTO_TWO = json.dumps({"queued": [
    {"company": "Acme", "title": "Engineer", "fit": "high"}, {"company": "Beta", "title": "Dev", "fit": "medium"},
]})
AUTO_NONE = json.dumps({"queued": []})


def run_with_auto(auto_stdout, queue_stdout, auto_code=0, queue_code=0, auto_raises=None):
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        if argv[1].endswith("auto_queue.py"):
            if auto_raises:
                raise auto_raises
            return SimpleNamespace(returncode=auto_code, stdout=auto_stdout, stderr="")
        return SimpleNamespace(returncode=queue_code, stdout=queue_stdout, stderr="")

    out = io.StringIO()
    with patch.object(nightly, "auto_queue_enabled", return_value=True), patch.object(nightly, "refresh_enabled", return_value=False), patch.object(nightly, "approval_enabled", return_value=False), \
         patch.object(nightly.subprocess, "run", side_effect=fake), \
         contextlib.redirect_stdout(out):
        code = nightly.main()
    return code, out.getvalue(), calls


class AutoQueueStepTests(unittest.TestCase):
    def test_off_by_default_so_only_the_drafting_command_runs(self):
        _, _, run = run_main(stdout=json.dumps(SUMMARY))
        self.assertEqual(run.call_count, 1)

    def test_queues_first_then_drafts_and_reports_both(self):
        code, text, calls = run_with_auto(AUTO_TWO, json.dumps(SUMMARY))
        self.assertEqual(code, 0)
        self.assertEqual([c[1].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for c in calls], ["auto_queue.py", "queue_cli.py"])
        self.assertIn("--json", calls[0])
        self.assertNotIn("--fill-forms", calls[1])
        self.assertTrue(text.startswith("Auto-queued 2 job(s):\n  high | Acme | Engineer\n  medium | Beta | Dev\nJob queue run: 1 drafted"))

    def test_nothing_queued_and_nothing_to_draft_stays_silent(self):
        code, text, calls = run_with_auto(AUTO_NONE, "Could not start: The queue is empty. Queue some jobs first.", queue_code=1)
        self.assertEqual((code, text), (0, ""))
        self.assertEqual(len(calls), 2)

    def test_a_failure_while_queueing_is_reported_but_drafting_still_runs(self):
        code, text, calls = run_with_auto("boom: seen_jobs.json unreadable", json.dumps(SUMMARY), auto_code=1)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("Auto-queue failed: boom", text)
        self.assertIn("1 drafted", text)

    def test_a_hang_while_queueing_is_reported_but_drafting_still_runs(self):
        code, text, calls = run_with_auto("", json.dumps(SUMMARY), auto_raises=subprocess.TimeoutExpired("x", 1))
        self.assertEqual((code, len(calls)), (0, 2))
        self.assertIn("Auto-queue failed", text)

    def test_refresh_runs_first_when_enabled(self):
        def fake(argv, **kwargs):
            name = argv[1].replace("\\", "/").rsplit("/", 1)[-1]
            out = {"refresh_cli.py": "Scrape: 4 new, 9 already known, 0 error(s).\nRanking: done.", "auto_queue.py": AUTO_NONE}.get(name, json.dumps(SUMMARY))
            return SimpleNamespace(returncode=0, stdout=out, stderr="")

        out = io.StringIO()
        with patch.object(nightly, "auto_queue_enabled", return_value=True), patch.object(nightly, "refresh_enabled", return_value=True), patch.object(nightly, "approval_enabled", return_value=False), \
             patch.object(nightly.subprocess, "run", side_effect=fake), contextlib.redirect_stdout(out):
            self.assertEqual(nightly.main(), 0)
        self.assertTrue(out.getvalue().startswith("Scrape: 4 new, 9 already known, 0 error(s).\nRanking: done.\nJob queue run: 1 drafted"))

    def test_the_switch_reads_queue_config(self):
        with patch.object(nightly.Path, "read_text", return_value=json.dumps({"auto_queue": {"enabled": True}})):
            self.assertTrue(nightly.auto_queue_enabled())
        with patch.object(nightly.Path, "read_text", return_value=json.dumps({"auto_queue": {"enabled": False}})):
            self.assertFalse(nightly.auto_queue_enabled())
        with patch.object(nightly.Path, "read_text", return_value="not json"):
            self.assertFalse(nightly.auto_queue_enabled())
        with patch.object(nightly.Path, "read_text", side_effect=OSError):
            self.assertFalse(nightly.auto_queue_enabled())


if __name__ == "__main__":
    unittest.main()

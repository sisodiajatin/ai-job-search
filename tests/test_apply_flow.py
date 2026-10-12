"""Verify the draft-to-browser handoff without model calls or employer uploads."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import apply_flow
from api.launcher import build_launch
from api.run_lock import application_lock
from api.launcher import LaunchError


class ApplyFlowTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://example.com/jobs/123"
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for directory in ("cv", "cover_letters"):
            (self.root / directory).mkdir()
        self.cv = self.root / "cv" / "tailored.pdf"
        self.cover = self.root / "cover_letters" / "tailored.pdf"
        self.cv.write_bytes(b"%PDF-1.4")
        self.cover.write_bytes(b"%PDF-1.4")
        self.result = {"status": "drafted", "cv": "cv/tailored.pdf", "cover": "cover_letters/tailored.pdf"}
        self.seen = {"seen": {self.url: {"company": "Example", "title": "Engineer", "url": self.url}}}

    @staticmethod
    def two_phase(result):
        """Apply now evaluates first (the user approves), then drafts."""
        def fake(item, config, emit=None, *, phase="auto", run_id=None):
            if phase == "evaluate":
                return {"status": "evaluated", "evaluation": {"score": 70}}
            return result
        return fake

    def run_flow(self, result):
        with patch.object(apply_flow, "REPO", self.root), \
             patch.object(apply_flow, "load_seen", return_value=self.seen), \
             patch.object(apply_flow.queue_runner, "load_config", return_value={}), \
             patch.object(apply_flow.queue_runner, "run_job", side_effect=self.two_phase(result)), \
             patch.object(apply_flow, "ask_proceed", return_value=True), \
             patch.object(apply_flow.subprocess, "call", return_value=0) as browser:
            code = apply_flow.run(self.url)
            return code, browser

    def test_success_passes_exact_new_documents(self):
        code, browser = self.run_flow(self.result)
        self.assertEqual(code, 0)
        argv = browser.call_args.args[0]
        self.assertEqual(argv[2:], [self.url, "--draft", "--cv", str(self.cv), "--cover", str(self.cover)])
        self.assertEqual(browser.call_args.kwargs["cwd"], str(self.root))

    def test_failed_or_skipped_never_opens_browser(self):
        for status, expected in (("failed", 1), ("skipped", 0), ("stopped", 1)):
            with self.subTest(status=status):
                code, browser = self.run_flow({"status": status, "reason": "Test stop"})
                self.assertEqual(code, expected)
                browser.assert_not_called()

    def test_missing_cover_stops_before_browser(self):
        with patch.object(apply_flow, "REPO", self.root), \
             patch.object(apply_flow, "load_seen", return_value=self.seen), \
             patch.object(apply_flow.queue_runner, "load_config", return_value={}), \
             patch.object(apply_flow.queue_runner, "run_job", side_effect=self.two_phase({**self.result, "cover": None})), \
             patch.object(apply_flow, "ask_proceed", return_value=True), \
             patch.object(apply_flow.subprocess, "call") as browser:
            with self.assertRaises(ValueError):
                apply_flow.run(self.url)
            browser.assert_not_called()

    def test_runner_rejects_success_result_from_failed_process(self):
        runner = apply_flow.queue_runner
        proc = MagicMock(returncode=1, stdout=[])
        with patch.object(runner, "RESULTS_DIR", self.root), \
             patch.object(runner, "REPO", self.root), \
             patch.object(runner, "claude_bin", return_value="claude"), \
             patch.object(runner.subprocess, "Popen", return_value=proc), \
             patch.object(runner.threading, "Timer"), \
             patch.object(runner, "_cleanup_build_artifacts"), \
             patch.object(runner, "_read_result", return_value=self.result), \
             patch.object(runner, "record_auto_result") as record:
            result = runner.run_job({"url": self.url, "company": "Example", "title": "Engineer"}, runner.DEFAULT_CONFIG)
        self.assertEqual(result["status"], "failed")
        record.assert_not_called()

    def test_pdf_path_cannot_escape_expected_directory(self):
        with patch.object(apply_flow, "REPO", self.root):
            for value in ("../outside.pdf", "cover_letters/tailored.pdf", "cv/missing.pdf", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    apply_flow.checked_pdf(value, "cv")

    def test_launcher_passes_url_as_data(self):
        argv, env = build_launch(self.url)
        self.assertNotIn(self.url, argv[-1])
        self.assertEqual(env["JOBDESK_APPLY_URL"], self.url)
        self.assertTrue(env["JOBDESK_APPLY_SCRIPT"].endswith("apply_flow.py"))

    def test_duplicate_run_is_rejected_and_lock_is_released(self):
        errors = []

        def duplicate():
            try:
                with application_lock(self.url, self.root):
                    errors.append("unexpected success")
            except LaunchError as err:
                errors.append(err.status)

        with application_lock(self.url, self.root):
            # The outer flow and inner drafting runner can share the lock.
            with application_lock(self.url, self.root):
                thread = threading.Thread(target=duplicate)
                thread.start()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [409])
        with application_lock(self.url, self.root):
            pass


if __name__ == "__main__":
    unittest.main()

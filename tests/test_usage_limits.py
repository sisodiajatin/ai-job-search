"""A Claude usage limit stops the job with a clear message. There is no second provider."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from api import providers, queue_runner as runner
from apply_forms import draft

LIMIT = [{"type": "result", "is_error": True, "errors": ["You've hit your limit · resets tomorrow"]}]


class DetectionTests(unittest.TestCase):
    def test_windows_json_result_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({"status": "drafted"}), encoding="utf-8-sig")
            self.assertEqual(runner._read_result(path), {"status": "drafted"})

    def test_usage_limit_errors_are_recognized(self):
        for event in (
            {"type": "assistant", "error": "rate_limit"},
            {"type": "result", "is_error": True, "errors": ["You've hit your limit · resets tomorrow"]},
            {"type": "error", "message": "HTTP 429 Too Many Requests"},
        ):
            with self.subTest(event=event):
                self.assertTrue(providers.limit_event(event))

    def test_posting_text_tool_output_and_budget_caps_are_not_usage_limits(self):
        for event in (
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "You've hit your limit"}]}},
            {"type": "user", "message": "rate_limit"},
            {"type": "result", "is_error": True, "errors": ["Maximum budget exceeded"]},
            {"type": "result", "is_error": True, "errors": ["Authentication failed"]},
        ):
            with self.subTest(event=event):
                self.assertFalse(providers.limit_event(event))

    def test_nothing_about_a_second_provider_remains(self):
        for name in ("codex_bin", "codex_command", "provider_chain", "claude_attempt_budget"):
            self.assertFalse(hasattr(providers, name), name)
        for key in ("fallback_provider", "codex_model", "max_provider_switches"):
            self.assertNotIn(key, runner.DEFAULT_CONFIG)
        shipped = json.loads((Path(runner.CONFIG_PATH)).read_text(encoding="utf-8"))
        self.assertTrue(set(shipped).isdisjoint({"fallback_provider", "codex_model", "max_provider_switches"}))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.results = self.root / "job_scraper" / "auto_results"
        (self.root / "cv").mkdir()
        (self.root / "cover_letters").mkdir()
        self.item = {"url": "https://example.com/job", "company": "Example", "title": "Engineer"}
        self.config = dict(runner.DEFAULT_CONFIG)
        self.calls = []

    def run_with(self, events, code, result):
        def launch(argv, **kwargs):
            self.calls.append(argv)
            prompt = argv[argv.index("-p") + 1]
            (self.root / re.search(r"--checkpoint ([^ ]+)", prompt).group(1)).write_text(json.dumps({"completed_steps": [0, 1]}))
            if result is not None:
                (self.root / re.search(r"--result ([^ ]+)", prompt).group(1)).write_text(json.dumps(result))
            proc = MagicMock(returncode=code, stdout=[json.dumps(e) for e in events])
            proc.poll.return_value = code
            return proc

        with patch.object(runner, "REPO", self.root), patch.object(runner, "RESULTS_DIR", self.results), \
             patch.object(runner, "CV_DIR", self.root / "cv"), patch.object(runner, "COVER_DIR", self.root / "cover_letters"), \
             patch.object(runner, "claude_bin", return_value="claude"), \
             patch.object(runner.subprocess, "Popen", side_effect=launch), patch.object(runner.threading, "Timer"), \
             patch.object(runner.protected, "snapshot", return_value={}), patch.object(runner.protected, "changed", return_value=[]), \
             patch.object(runner.finalize, "finalize", return_value={"ok": False, "reason": "no checkpoint"}), \
             patch.object(runner, "record_auto_result") as record, patch.dict(runner._state, stop_requested=False):
            outcome = runner.run_job(self.item, self.config, emit=lambda _m: None)
        return outcome, record

    def test_a_usage_limit_stops_the_job_with_one_attempt_and_a_clear_reason(self):
        outcome, record = self.run_with(LIMIT, 1, None)
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("usage limit", outcome["reason"])
        self.assertIn("run it again", outcome["reason"])
        self.assertEqual(len(self.calls), 1, "no second process may be started")
        self.assertEqual([a["provider"] for a in outcome["attempts"]], ["claude"])
        self.assertTrue(list(self.results.glob("*_progress.json")), "progress is kept")
        record.assert_not_called()

    def test_a_budget_cap_or_an_ordinary_failure_is_not_called_a_usage_limit(self):
        for events in ([{"type": "result", "is_error": True, "errors": ["Maximum budget exceeded"]}], []):
            with self.subTest(events=events):
                self.calls = []
                outcome, _ = self.run_with(events, 1, None)
                self.assertEqual(outcome["status"], "failed")
                self.assertNotIn("usage limit", outcome["reason"])
                self.assertEqual(len(self.calls), 1)

    def test_success_and_skip_run_once(self):
        for status in ("drafted", "skipped"):
            with self.subTest(status=status):
                self.calls = []
                outcome, _ = self.run_with([], 0, {"status": status, "cv": "cv/a.pdf", "cover": "cover_letters/a.pdf"})
                self.assertEqual(outcome["status"], status)
                self.assertEqual(len(self.calls), 1)

    def test_the_whole_job_budget_goes_to_the_single_attempt(self):
        self.run_with([], 0, {"status": "skipped"})
        budget = float(self.calls[0][self.calls[0].index("--max-budget-usd") + 1])
        self.assertEqual(budget, float(self.config["per_job_budget_usd"]))
        self.assertGreaterEqual(budget, 4.0)


class AnswerDraftingTests(unittest.TestCase):
    def draft(self, **proc):
        with patch.object(draft.shutil, "which", return_value="claude"), \
             patch.object(draft.subprocess, "run", return_value=SimpleNamespace(**proc)) as run:
            try:
                return draft.draft_answers(["Why us?"], "Example", "Engineer", None, Path.cwd()), run
            except RuntimeError as err:
                return err, run

    def test_answers_come_from_one_read_only_claude_call(self):
        answers, run = self.draft(returncode=0, stderr="", stdout=json.dumps({"result": json.dumps({"Why us?": "I build products."})}))
        self.assertEqual(answers, {"Why us?": "I build products."})
        argv = run.call_args.args[0]
        self.assertEqual(run.call_count, 1)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")

    def test_a_usage_limit_raises_a_clear_message_without_retrying(self):
        err, run = self.draft(returncode=1, stderr="", stdout=json.dumps({"is_error": True, "errors": ["You've hit your limit"]}))
        self.assertIsInstance(err, RuntimeError)
        self.assertIn("usage limit", str(err))
        self.assertEqual(run.call_count, 1)

    def test_other_failures_raise_without_retrying(self):
        err, run = self.draft(returncode=1, stderr="", stdout=json.dumps({"is_error": True, "errors": ["Please log in"]}))
        self.assertIsInstance(err, RuntimeError)
        self.assertNotIn("usage limit", str(err))
        self.assertEqual(run.call_count, 1)

    def test_no_questions_means_no_call(self):
        with patch.object(draft.subprocess, "run") as run:
            self.assertEqual(draft.draft_answers([], "Example", "Engineer", None, Path.cwd()), {})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

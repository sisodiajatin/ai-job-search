"""Fit-evaluation approval, protected-file guard and budget split. No model calls, no employer uploads."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import apply_flow
from api import protected
from api import queue_runner as runner

EVALUATION = {
    "score": 72,
    "verdict": "Good Fit",
    "eligibility": "unverified",
    "eligibility_quote": "",
    "dimensions": {
        "technical": {"score": 80, "note": "React and Python match"},
        "experience": {"score": 65, "note": "about one year"},
        "behavioral": {"score": 70, "note": ""},
        "location": {"result": "PASS", "note": "relocating"},
        "career": {"score": 75, "note": ""},
    },
    "strengths": ["Stack match"],
    "gaps": ["No PostgreSQL"],
    "recommendation": "Apply and address the gaps.",
}


class ProtectedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for rel in ("CLAUDE.md", ".claude/commands/apply.md", "web/app.py", "templates/cv/x/template.tex"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original " + rel)
        (self.root / "cv").mkdir()
        (self.root / "cv" / "main_job.tex").write_text("drafts are not protected")
        (self.root / "web" / "__pycache__").mkdir()
        (self.root / "web" / "__pycache__" / "app.py").write_text("ignored")

    def test_drafts_and_pycache_are_not_protected(self):
        names = protected.snapshot(self.root)
        self.assertIn("CLAUDE.md", names)
        self.assertIn(".claude/commands/apply.md", names)
        self.assertNotIn("cv/main_job.tex", names)
        self.assertNotIn("web/__pycache__/app.py", names)

    def test_no_change_reports_nothing(self):
        before = protected.snapshot(self.root)
        (self.root / "cv" / "main_job.tex").write_text("edited draft")
        self.assertEqual(protected.changed(before, self.root), [])

    def test_modified_added_and_removed_are_reported_and_originals_saved(self):
        before = protected.snapshot(self.root)
        (self.root / "CLAUDE.md").write_text("tampered")
        (self.root / "web" / "evil.py").write_text("print('x')")
        (self.root / ".claude" / "commands" / "apply.md").unlink()
        problems = protected.changed(before, self.root)
        self.assertEqual(
            problems,
            ["modified: CLAUDE.md", "removed: .claude/commands/apply.md", "added: web/evil.py"],
        )
        backup = protected.backup_originals(before, problems, self.root / "backup")
        self.assertEqual((backup / "CLAUDE.md").read_text(), "original CLAUDE.md")
        self.assertEqual((backup / ".claude/commands/apply.md").read_text(), "original .claude/commands/apply.md")
        self.assertFalse((backup / "web" / "evil.py").exists())


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.results = self.root / "job_scraper" / "auto_results"
        (self.root / "cv").mkdir()
        (self.root / "cover_letters").mkdir()
        self.item = {"url": "https://example.com/job", "company": "Example", "title": "Engineer"}
        self.config = dict(runner.DEFAULT_CONFIG)
        self.argvs = []

    def run_phase(self, phase, result, *, run_id="job_run", tampered=None):
        def launch(argv, **kwargs):
            self.argvs.append(argv)
            prompt = argv[argv.index("-p") + 1]
            rel = re.search(r"--result ([^ ]+)", prompt).group(1)
            (self.root / rel).write_text(json.dumps(result))
            proc = MagicMock(returncode=0, stdout=[json.dumps({"type": "result", "total_cost_usd": 0.2})])
            proc.poll.return_value = 0
            return proc

        with patch.object(runner, "REPO", self.root), \
             patch.object(runner, "RESULTS_DIR", self.results), \
             patch.object(runner, "CV_DIR", self.root / "cv"), \
             patch.object(runner, "COVER_DIR", self.root / "cover_letters"), \
             patch.object(runner, "claude_bin", return_value="claude"), \
             patch.object(runner.subprocess, "Popen", side_effect=launch), \
             patch.object(runner.threading, "Timer"), \
             patch.object(runner, "record_auto_result") as record, \
             patch.object(runner.protected, "snapshot", return_value={}), \
             patch.object(runner.protected, "changed", return_value=tampered or []), \
             patch.object(runner.protected, "backup_originals", return_value=self.results / "protected_backup" / run_id), \
             patch.dict(runner._state, stop_requested=False):
            outcome = runner.run_job(self.item, self.config, emit=lambda _m: None, phase=phase, run_id=run_id)
        return outcome, record

    def prompt(self, index=0):
        argv = self.argvs[index]
        return argv[argv.index("-p") + 1]

    def test_evaluate_phase_stops_after_step_one_and_returns_the_evaluation(self):
        outcome, record = self.run_phase("evaluate", {"status": "evaluated", "evaluation": EVALUATION})
        self.assertEqual(outcome["status"], "evaluated")
        self.assertEqual(outcome["evaluation"]["score"], 72)
        self.assertIn("--evaluate-only", self.prompt())
        self.assertNotIn("--approved", self.prompt())
        record.assert_not_called()

    def test_draft_phase_is_marked_approved_and_shares_the_checkpoint(self):
        self.run_phase("evaluate", {"status": "evaluated", "evaluation": EVALUATION}, run_id="shared")
        outcome, record = self.run_phase("draft", {"status": "drafted", "cv": "cv/a.pdf", "cover": "cover_letters/a.pdf", "score": 72}, run_id="shared")
        self.assertEqual(outcome["status"], "drafted")
        self.assertIn("--approved", self.prompt(1))
        self.assertNotIn("--evaluate-only", self.prompt(1))
        progress = [re.search(r"--checkpoint ([^ ]+)", self.prompt(i)).group(1) for i in (0, 1)]
        self.assertEqual(progress[0], progress[1])
        record.assert_called_once()

    def test_evaluate_phase_rejects_a_drafted_result(self):
        outcome, _ = self.run_phase("evaluate", {"status": "drafted"})
        self.assertEqual(outcome["status"], "failed")  # wrong status for the evaluate phase is rejected

    def test_auto_phase_is_unchanged(self):
        outcome, _ = self.run_phase("auto", {"status": "skipped", "score": 40, "reason": "below floor"})
        self.assertEqual(outcome["status"], "skipped")
        self.assertNotIn("--approved", self.prompt())
        self.assertNotIn("--evaluate-only", self.prompt())

    def test_unknown_phase_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.run_job(self.item, self.config, phase="surprise")

    def test_edits_to_protected_files_fail_the_job_and_discard_the_result(self):
        outcome, record = self.run_phase(
            "draft",
            {"status": "drafted", "cv": "cv/a.pdf", "cover": "cover_letters/a.pdf"},
            tampered=["modified: CLAUDE.md"],
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("Protected files changed", outcome["reason"])
        self.assertIn("modified: CLAUDE.md", outcome["reason"])
        record.assert_not_called()

    def test_default_budget_leaves_room_for_a_normal_job(self):
        self.assertGreaterEqual(float(self.config["per_job_budget_usd"]), 4.0)  # a typical drafted job cost about $2.90
        self.assertGreaterEqual(float(self.config["evaluation_budget_usd"]), 1.0)  # a real evaluation cost about $0.63


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://example.com/jobs/123"
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "cv").mkdir()
        (self.root / "cover_letters").mkdir()
        self.cv = self.root / "cv" / "tailored.pdf"
        self.cover = self.root / "cover_letters" / "tailored.pdf"
        self.cv.write_bytes(b"%PDF-1.4")
        self.cover.write_bytes(b"%PDF-1.4")
        self.seen = {"seen": {self.url: {"company": "Example", "title": "Engineer", "url": self.url}}}
        self.calls = []

    def flow(self, *, evaluation_status="evaluated", answer=True, draft_status="drafted"):
        def fake_run_job(item, config, emit=None, *, phase="auto", run_id=None):
            self.calls.append((phase, run_id))
            if phase == "evaluate":
                return {"status": evaluation_status, "evaluation": EVALUATION, "reason": "evaluation problem"}
            return {"status": draft_status, "cv": "cv/tailored.pdf", "cover": "cover_letters/tailored.pdf", "reason": "x"}

        with patch.object(apply_flow, "REPO", self.root), \
             patch.object(apply_flow, "load_seen", return_value=self.seen), \
             patch.object(apply_flow.queue_runner, "load_config", return_value={"score_floor": 60}), \
             patch.object(apply_flow.queue_runner, "run_job", side_effect=fake_run_job), \
             patch.object(apply_flow, "ask_proceed", return_value=answer) as ask, \
             patch.object(apply_flow.subprocess, "call", return_value=0) as browser, \
             patch("builtins.print") as shown:
            code = apply_flow.run(self.url)
        return code, ask, browser, shown

    def test_yes_drafts_with_the_same_run_id_then_fills_the_form(self):
        code, ask, browser, shown = self.flow(answer=True)
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in self.calls], ["evaluate", "draft"])
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        ask.assert_called_once()
        self.assertEqual(browser.call_args.args[0][2:], [self.url, "--draft", "--cv", str(self.cv), "--cover", str(self.cover)])
        printed = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
        self.assertIn("FIT EVALUATION", printed)
        self.assertIn("72/100", printed)

    def test_no_means_nothing_is_drafted_and_no_browser_opens(self):
        code, ask, browser, _ = self.flow(answer=False)
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in self.calls], ["evaluate"])
        browser.assert_not_called()

    def test_failed_evaluation_never_asks_or_drafts(self):
        code, ask, browser, _ = self.flow(evaluation_status="failed")
        self.assertEqual(code, 1)
        ask.assert_not_called()
        self.assertEqual([c[0] for c in self.calls], ["evaluate"])
        browser.assert_not_called()

    def test_failed_draft_after_approval_never_opens_the_form(self):
        code, _, browser, _ = self.flow(answer=True, draft_status="failed")
        self.assertEqual(code, 1)
        browser.assert_not_called()

    def test_format_shows_eligibility_failure_and_low_score_clearly(self):
        low = {**EVALUATION, "score": 41, "eligibility": "fail", "eligibility_quote": "US citizens only"}
        text = apply_flow.format_evaluation({"title": "Dev", "company": "Acme"}, low, 60)
        self.assertIn("Eligibility gate: FAIL", text)
        self.assertIn('"US citizens only"', text)
        self.assertIn("Proceeding is your call", text)
        self.assertIn("below your usual score floor of 60", text)
        self.assertIn("PostgreSQL", text)

    def test_end_of_input_counts_as_no(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(apply_flow.ask_proceed("? "))
        with patch("builtins.input", return_value=" Yes "):
            self.assertTrue(apply_flow.ask_proceed("? "))
        with patch("builtins.input", return_value="sure"):
            self.assertFalse(apply_flow.ask_proceed("? "))


if __name__ == "__main__":
    unittest.main()

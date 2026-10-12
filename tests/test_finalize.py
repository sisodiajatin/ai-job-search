"""Runner-side compile and mechanical checks for drafts a model could not compile. No real LaTeX here."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from api import finalize
from api import queue_runner as runner

GOOD_CV = """Jatin Sisodia
Columbia, MD | +1 774-701-4370 | jatinsisodiausa@gmail.com
Software Engineer, WebCraft-IT                         Aug 2023 - Jun 2024
Worcester Polytechnic Institute                        Aug 2024 - May 2026
"""


class CheckTests(unittest.TestCase):
    def test_a_clean_cv_passes(self):
        self.assertEqual(finalize.check_cv(GOOD_CV, 1, 1), [])

    def test_wrong_page_count_fails(self):
        self.assertTrue(any("exactly 1" in p for p in finalize.check_cv(GOOD_CV, 2, 1)))

    def test_missing_contact_details_fail(self):
        text = GOOD_CV.replace("jatinsisodiausa@gmail.com", "").replace("774-701-4370", "")
        problems = finalize.check_cv(text, 1, 1)
        self.assertTrue(any("email" in p for p in problems))
        self.assertTrue(any("phone" in p for p in problems))

    def test_en_dash_dates_fail_but_plain_hyphens_pass(self):
        self.assertTrue(any("en-dash" in p for p in finalize.check_cv(GOOD_CV.replace("Aug 2023 - Jun 2024", "Aug 2023 – Jun 2024"), 1, 1)))
        self.assertTrue(any("en-dash" in p for p in finalize.check_cv(GOOD_CV.replace("Aug 2024 - May 2026", "Aug 2024 – May 2026"), 1, 1)))
        self.assertEqual(finalize.check_cv(GOOD_CV, 1, 1), [])

    def test_ruled_out_phrases_fail(self):
        for phrase in ("Available to start October 10, 2026", "willing to relocate to New York", "Recent MS graduate in CS",
                       "Configured a job-search automation framework"):
            with self.subTest(phrase=phrase):
                self.assertTrue(any("ruled out" in p for p in finalize.check_cv(GOOD_CV + phrase, 1, 1)))

    def test_ordinary_uses_of_those_words_do_not_fail(self):
        text = GOOD_CV + "Built highly available services and indexing pipelines for recent releases"
        self.assertEqual(finalize.check_cv(text, 1, 1), [])

    def test_garbled_or_empty_text_layer_fails(self):
        self.assertTrue(finalize.check_cv("(cid:12) " + GOOD_CV, 1, 1))
        self.assertTrue(any("could not be extracted" in p for p in finalize.check_cv("", 1, 1)))

    def test_cover_checks(self):
        self.assertEqual(finalize.check_cover("Dear Hiring Manager, I am available from October 10.", 1), [])
        self.assertTrue(finalize.check_cover("hello", 2))
        self.assertTrue(finalize.check_cover("I run a job search automation pipeline", 1))

    def test_page_count_survives_tex_log_line_wrapping(self):
        wrapped = "Output written on cover_a_very_long_file_name_2026_us.pdf (1 pag" + chr(10) + "e)."
        self.assertEqual(finalize.page_count(Path("missing.pdf"), wrapped), 1)
        self.assertEqual(finalize.page_count(Path("missing.pdf"), "Output written on x.pdf (2 pages, 9 bytes)."), 2)
        self.assertIsNone(finalize.page_count(Path("missing.pdf"), "nothing useful"))


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "cv").mkdir()
        (self.root / "cover_letters").mkdir()
        (self.root / "cv" / "main_a.tex").write_text("cv source")
        (self.root / "cover_letters" / "cover_a.tex").write_text("cover source")
        self.progress = {
            "completed_steps": [0, 1, 2, 3, 4],
            "artifact_paths": {"cv_source": "cv/main_a.tex", "cover_source": "cover_letters/cover_a.tex"},
            "verification_results": {"fit": {"overall": 77}},
        }

    def run_finalize(self, progress=None, *, compiles=(True, True), cv_pages=1, cover_pages=1, started=0.0, cv_text=GOOD_CV):
        results = iter([(compiles[0], cv_pages if compiles[0] else "cv engine failed"),
                        (compiles[1], cover_pages if compiles[1] else "xelatex failed")])
        with patch.object(finalize, "REPO", self.root), patch.object(finalize, "CV_DIR", self.root / "cv"), \
             patch.object(finalize, "COVER_DIR", self.root / "cover_letters"), \
             patch.object(finalize, "cv_settings", return_value=("pdflatex", 1)), \
             patch.object(finalize, "compile_tex", side_effect=lambda *a, **k: next(results)) as compile_tex, \
             patch.object(finalize, "pdf_text", side_effect=lambda pdf: cv_text if "main_" in pdf.name else "Dear Hiring Manager"):
            outcome = finalize.finalize(self.progress if progress is None else progress, started)
        return outcome, compile_tex

    def test_finished_sources_compile_and_pass(self):
        outcome, compile_tex = self.run_finalize()
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["cv"], "cv/main_a.pdf")
        self.assertEqual(outcome["cover"], "cover_letters/cover_a.pdf")
        self.assertEqual(outcome["score"], 77)
        engines = [call.args[1] for call in compile_tex.call_args_list]
        self.assertEqual(engines, ["pdflatex", "xelatex"])

    def test_refuses_without_proof_that_steps_zero_to_four_finished(self):
        for steps in ([], [0, 1], [0, 1, 2, 3]):
            with self.subTest(steps=steps):
                outcome, compile_tex = self.run_finalize({**self.progress, "completed_steps": steps})
                self.assertFalse(outcome["ok"])
                compile_tex.assert_not_called()

    def test_refuses_sources_outside_the_expected_folders_or_missing(self):
        for paths in ({"cv_source": "../evil.tex", "cover_source": "cover_letters/cover_a.tex"},
                      {"cv_source": "cover_letters/cover_a.tex", "cover_source": "cover_letters/cover_a.tex"},
                      {"cv_source": "cv/main_a.tex"},
                      {"cv_source": "cv/missing.tex", "cover_source": "cover_letters/cover_a.tex"},
                      {"cv_source": "cv/main_a.pdf", "cover_source": "cover_letters/cover_a.tex"}):
            with self.subTest(paths=paths):
                outcome, compile_tex = self.run_finalize({**self.progress, "artifact_paths": paths})
                self.assertFalse(outcome["ok"])
                compile_tex.assert_not_called()

    def test_refuses_sources_older_than_this_job(self):
        outcome, compile_tex = self.run_finalize(started=9e12)
        self.assertFalse(outcome["ok"])
        compile_tex.assert_not_called()

    def test_compile_failure_and_failed_checks_are_not_rescued(self):
        self.assertIn("CV compile failed", self.run_finalize(compiles=(False, True))[0]["reason"])
        self.assertIn("cover letter compile failed", self.run_finalize(compiles=(True, False))[0]["reason"])
        self.assertIn("mechanical checks failed", self.run_finalize(cv_pages=2)[0]["reason"])
        self.assertIn("mechanical checks failed", self.run_finalize(cover_pages=2)[0]["reason"])
        self.assertIn("ruled out", self.run_finalize(cv_text=GOOD_CV + "Available to start soon")[0]["reason"])

    def test_no_checkpoint(self):
        self.assertFalse(finalize.finalize(None, 0)["ok"])


class RunnerRescueTests(unittest.TestCase):
    """A model that finished the drafts but could not compile must not leave the job 'failed'."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.results = self.root / "job_scraper" / "auto_results"
        (self.root / "cv").mkdir()
        (self.root / "cover_letters").mkdir()
        self.item = {"url": "https://example.com/job", "company": "Example", "title": "Engineer"}

    def run_job(self, *, finalize_result, phase="draft", status="failed"):
        def launch(argv, **kwargs):
            prompt = argv[argv.index("-p") + 1]
            result_rel = re.search(r"--result ([^ ]+)", prompt).group(1)
            (self.root / result_rel).write_text(json.dumps({"status": status, "reason": "compile blocked: sandbox"}))
            proc = MagicMock(returncode=0, stdout=[json.dumps({"type": "result", "total_cost_usd": 0.1})])
            proc.poll.return_value = 0
            return proc

        with patch.object(runner, "REPO", self.root), patch.object(runner, "RESULTS_DIR", self.results), \
             patch.object(runner, "CV_DIR", self.root / "cv"), patch.object(runner, "COVER_DIR", self.root / "cover_letters"), \
             patch.object(runner, "claude_bin", return_value="claude"), \
             patch.object(runner.subprocess, "Popen", side_effect=launch), patch.object(runner.threading, "Timer"), \
             patch.object(runner.protected, "snapshot", return_value={}), patch.object(runner.protected, "changed", return_value=[]), \
             patch.object(runner.finalize, "finalize", return_value=finalize_result) as fin, \
             patch.object(runner, "record_auto_result") as record, patch.dict(runner._state, stop_requested=False):
            outcome = runner.run_job(self.item, dict(runner.DEFAULT_CONFIG), emit=lambda _m: None, phase=phase, run_id="job")
        return outcome, fin, record

    def test_blocked_compile_is_rescued_with_an_honest_warning(self):
        outcome, fin, record = self.run_job(finalize_result={"ok": True, "cv": "cv/a.pdf", "cover": "cover_letters/a.pdf", "score": 77})
        self.assertEqual(outcome["status"], "drafted")
        self.assertEqual((outcome["cv"], outcome["cover"], outcome["score"]), ("cv/a.pdf", "cover_letters/a.pdf", 77))
        warning = " ".join(outcome["warnings"])
        self.assertIn("not by a model", warning)
        self.assertIn("NOT run by a model", warning)
        self.assertIn("compile blocked: sandbox", warning)
        self.assertEqual(record.call_args.args[1]["status"], "drafted")

    def test_unrescuable_failure_stays_failed_with_the_reason(self):
        outcome, _, record = self.run_job(finalize_result={"ok": False, "reason": "CV compile failed: boom"})
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("CV compile failed: boom", " ".join(outcome["warnings"]))
        for call in record.call_args_list:
            self.assertNotEqual(call.args[1]["status"], "drafted")

    def test_evaluation_runs_are_never_rescued(self):
        _, fin, _ = self.run_job(finalize_result={"ok": True}, phase="evaluate")
        fin.assert_not_called()

    def test_a_job_that_drafted_normally_is_not_touched(self):
        outcome, fin, _ = self.run_job(finalize_result={"ok": True}, status="drafted")
        self.assertEqual(outcome["status"], "drafted")
        fin.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Run queue and fill forms: after drafting, each drafted job's employer form is filled one at a time.

No browser, no model, no real files: drafting and the form filler are replaced by fakes. The point is the wiring:
which jobs get a form, in what order, with which exact PDFs, and that nothing here ever submits.
"""

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from fastapi.testclient import TestClient

import apply_flow
from api import app as api_app, queue_runner

PDF = b"%PDF-1.4\n% fixture\n%%EOF\n"
A, B, C = "https://jobs.example.com/a", "https://jobs.example.com/b", "https://jobs.example.com/c"


class FillFormsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="queue_fill_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ("cv", "cover_letters", "job_scraper/auto_results"):
            (self.root / name).mkdir(parents=True)
        self.queue = [{"url": u, "company": f"Co {u[-1].upper()}", "title": "Engineer"} for u in (A, B, C)]
        self.drafting = {A: "drafted", B: "skipped", C: "drafted"}
        self.fills: list[list[str]] = []
        self.form_info: dict[str, dict] = {}  # url -> what _form_attempt returns

        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (queue_runner, apply_flow):
            stack.enter_context(patch.object(module, "REPO", self.root))
        stack.enter_context(patch.object(queue_runner, "RESULTS_DIR", self.root / "job_scraper/auto_results"))
        stack.enter_context(patch.object(queue_runner, "list_queue", side_effect=lambda: list(self.queue)))
        stack.enter_context(patch.object(queue_runner, "dequeue_job", side_effect=self._dequeue))
        stack.enter_context(patch.object(queue_runner, "load_config", side_effect=lambda: dict(queue_runner.DEFAULT_CONFIG)))
        stack.enter_context(patch.object(queue_runner, "run_job", side_effect=self._draft))
        stack.enter_context(patch.object(queue_runner, "_form_attempt", side_effect=self._fill))

    # --- fakes -------------------------------------------------------------------------------------------------
    def _dequeue(self, url):
        self.queue = [i for i in self.queue if i["url"] != url]
        return list(self.queue)

    def _draft(self, item, config, emit=None, **_):
        status = self.drafting[item["url"]]
        base = {"url": item["url"], "company": item["company"], "title": item["title"], "status": status}
        if status != "drafted":
            return {**base, "reason": "below the floor" if status == "skipped" else "provider error"}
        slug = item["url"][-1]
        (self.root / f"cv/main_{slug}.pdf").write_bytes(PDF)
        (self.root / f"cover_letters/cover_{slug}.pdf").write_bytes(PDF)
        return {**base, "score": 80, "cv": f"cv/main_{slug}.pdf", "cover": f"cover_letters/cover_{slug}.pdf"}

    def _fill(self, argv, deadline, emit):
        self.fills.append(argv)
        return self.form_info.get(argv[2], {"returncode": 0})

    def run_queue(self, fill_forms: bool, **config) -> list[dict]:
        queue_runner._state.update(
            running=True, stop_requested=False, fill_forms=fill_forms, results=[], events=[], next_id=1, current=None, message="",
        )
        with patch.object(queue_runner, "load_config", side_effect=lambda: {**queue_runner.DEFAULT_CONFIG, **config}):
            queue_runner._run()
        return queue_runner._state["results"]

    # --- behaviour ---------------------------------------------------------------------------------------------
    def test_drafts_only_by_default_and_no_form_is_touched(self):
        results = self.run_queue(fill_forms=False)
        self.assertEqual([r["status"] for r in results], ["drafted", "skipped", "drafted"])
        self.assertEqual(self.fills, [])
        self.assertTrue(all("form" not in r for r in results))

    def test_fills_only_drafted_jobs_in_queue_order_with_their_own_exact_pdfs(self):
        results = self.run_queue(fill_forms=True)
        self.assertEqual(len(self.fills), 2)  # B was skipped, so it has no CV and no form
        for argv, slug, url in zip(self.fills, ("a", "c"), (A, C)):
            self.assertEqual(argv, queue_runner.fill_argv(
                url, self.root / f"cv/main_{slug}.pdf", self.root / f"cover_letters/cover_{slug}.pdf",
            ))
        self.assertEqual([r.get("form", {}).get("status") for r in results], ["filled", None, "filled"])

    def test_fill_command_never_submits_and_is_plain_argv(self):
        argv = queue_runner.fill_argv(A, self.root / "cv/x.pdf", self.root / "cover_letters/y.pdf")
        self.assertTrue(all(isinstance(part, str) for part in argv))
        self.assertTrue(argv[1].endswith("fill_form.py"))
        self.assertEqual(argv[2], A)
        self.assertIn("--draft", argv)
        self.assertFalse([part for part in argv if "submit" in part.lower()])
        # fill_form.py itself has no way to submit; this guards against anyone adding one to the queue path
        self.assertFalse([part for part in argv if part.startswith("--") and part not in {"--draft", "--cv", "--cover"}])

    def test_outcomes_map_to_clear_statuses(self):
        self.drafting = {A: "drafted", B: "drafted", C: "drafted"}
        self.form_info = {
            A: {"returncode": 2},
            B: {"returncode": 1, "last_line": "Chrome profile is locked"},
            C: {"timed_out": True},
        }
        forms = [r["form"] for r in self.run_queue(fill_forms=True)]
        self.assertEqual([f["status"] for f in forms], ["nothing_filled", "failed", "failed"])
        self.assertIn("sign-in", forms[0]["reason"])
        self.assertEqual(forms[1]["reason"], "Chrome profile is locked")
        self.assertIn("75 minutes", forms[2]["reason"])

    def test_a_failed_form_does_not_stop_the_next_or_count_as_a_drafting_failure(self):
        self.drafting = {A: "drafted", B: "drafted", C: "drafted"}
        self.form_info = {A: {"returncode": 1, "last_line": "boom"}, B: {"returncode": 1, "last_line": "boom"}}
        results = self.run_queue(fill_forms=True, max_consecutive_failures=1)
        self.assertEqual(len(self.fills), 3)
        self.assertEqual([r["form"]["status"] for r in results], ["failed", "failed", "filled"])
        self.assertTrue(all(r["status"] == "drafted" for r in results))  # form trouble never un-drafts a job

    def test_stop_during_a_form_leaves_the_rest_alone(self):
        self.drafting = {A: "drafted", B: "drafted", C: "drafted"}

        def stop_during_first(argv, deadline, emit):
            self.fills.append(argv)
            queue_runner._state["stop_requested"] = True
            return {"stopped": True}

        with patch.object(queue_runner, "_form_attempt", side_effect=stop_during_first):
            results = self.run_queue(fill_forms=True)
        self.assertEqual(len(self.fills), 1)
        self.assertEqual(results[0]["form"]["status"], "stopped")
        self.assertNotIn("form", results[1])

    def test_a_missing_pdf_is_reported_and_no_browser_is_opened(self):
        self.drafting = {A: "drafted", B: "skipped", C: "skipped"}
        original = self._draft

        def draft_then_lose_pdf(item, config, emit=None, **kw):
            outcome = original(item, config, emit, **kw)
            (self.root / "cover_letters/cover_a.pdf").unlink()
            return outcome

        with patch.object(queue_runner, "run_job", side_effect=draft_then_lose_pdf):
            results = self.run_queue(fill_forms=True)
        self.assertEqual(self.fills, [])
        self.assertEqual(results[0]["form"]["status"], "failed")
        self.assertIn("missing", results[0]["form"]["reason"].lower())

    def test_nothing_drafted_means_no_forms(self):
        self.drafting = {A: "skipped", B: "skipped", C: "failed"}
        self.run_queue(fill_forms=True)
        self.assertEqual(self.fills, [])
        self.assertTrue(any("no forms to fill" in e["message"] for e in queue_runner._state["events"]))


class QueueRunEndpointTests(unittest.TestCase):
    HEADERS = {"X-Job-Desk": "1", "Origin": "http://127.0.0.1:8787"}

    def post(self, **kwargs):
        client = TestClient(api_app.app, base_url="http://127.0.0.1:8787")
        with patch.object(queue_runner, "start", return_value={"running": True}) as start:
            response = client.post("/api/queue/run", headers=self.HEADERS, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return start

    def test_no_body_still_means_drafts_only(self):
        self.post().assert_called_once_with(fill_forms=False)

    def test_fill_forms_flag_is_passed_through(self):
        self.post(json={"fill_forms": True}).assert_called_once_with(fill_forms=True)
        self.post(json={"fill_forms": False}).assert_called_once_with(fill_forms=False)

    def test_other_sites_are_still_refused(self):
        client = TestClient(api_app.app, base_url="http://127.0.0.1:8787")
        response = client.post("/api/queue/run", headers={"X-Job-Desk": "1", "Origin": "https://evil.example"}, json={"fill_forms": True})
        self.assertGreaterEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

"""Connected Apply API → evaluation → draft → exact PDF handoff → real Chrome.

Providers and employer pages are local fixtures; no model usage or real uploads.
Set JOBDESK_TEST_LIVE=1 to watch the Chrome demo for 30 seconds.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import apply_flow
import fill_form
from api import app as api_app, launcher, paths, queue_runner, store
from apply_forms import draft, fill, resolve
try:  # unittest discovery puts tests/ on the path; pytest imports the package
    from test_live_form import FORM
except ImportError:
    from tests.test_live_form import FORM


class ConnectedApplicationTests(unittest.TestCase):
    def test_apply_draft_handoff_and_live_form(self):
        live = os.environ.get("JOBDESK_TEST_LIVE") == "1"
        listing = "https://demo.invalid/job/engineer"
        application = "https://demo.invalid/application"
        observed = []
        observed_evaluation = []
        messages = []

        with tempfile.TemporaryDirectory(prefix="job_desk_connected_") as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            for name in ("cv", "cover_letters", "job_scraper/auto_results", "web/apply_forms", "documents/applications"):
                (root / name).mkdir(parents=True)
            seen_path = root / "job_scraper/seen_jobs.json"
            queue_path = root / "job_scraper/apply_queue.json"
            seen_path.write_text(json.dumps({"seen": {listing: {"company": "Demo", "title": "Engineer", "url": listing}}}))
            queue_path.write_text(json.dumps({"queue": [{"url": listing, "company": "Demo", "title": "Engineer"}]}))
            (root / "web/apply_forms/profile_fields.json").write_text(json.dumps({"first_name": "Demo Candidate", "email": "demo@example.com"}))

            for module in (paths, store, queue_runner, apply_flow, launcher):
                stack.enter_context(patch.object(module, "REPO", root))
            for module in (paths, store, queue_runner):
                stack.enter_context(patch.object(module, "CV_DIR", root / "cv"))
                stack.enter_context(patch.object(module, "COVER_DIR", root / "cover_letters"))
            for module in (paths, store):
                stack.enter_context(patch.object(module, "APPLICATIONS_DIR", root / "documents/applications"))
                stack.enter_context(patch.object(module, "SEEN_JOBS", seen_path))
                stack.enter_context(patch.object(module, "APPLY_QUEUE", queue_path))
                stack.enter_context(patch.object(module, "TRACKER", root / "tracker.csv"))
            stack.enter_context(patch.object(queue_runner, "RESULTS_DIR", root / "job_scraper/auto_results"))
            stack.enter_context(patch.object(queue_runner, "load_config", return_value=dict(queue_runner.DEFAULT_CONFIG)))
            stack.enter_context(patch.object(fill_form, "WEB_DIR", root / "web"))
            stack.enter_context(patch.object(fill, "PROFILE_DIR", root / "browser-profile"))
            stack.enter_context(patch.dict(queue_runner._state, stop_requested=False))

            # Exercise the real guarded endpoint and Windows launcher, capturing
            # the terminal process so it cannot escape the isolated fixture.
            with patch.object(launcher.subprocess, "Popen") as terminal, patch.object(launcher.shutil, "which", return_value="claude"):
                client = TestClient(api_app.app, base_url="http://127.0.0.1:8787")
                response = client.post("/api/apply", json={"key": listing}, headers={"X-Job-Desk": "1", "Origin": "http://127.0.0.1:8787"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(terminal.call_args.kwargs["env"]["JOBDESK_APPLY_URL"], listing)
                self.assertTrue(terminal.call_args.kwargs["env"]["JOBDESK_APPLY_SCRIPT"].endswith("apply_flow.py"))
                self.assertEqual(response.json()["queue"], [])
            observed.append("Apply API launched flow")

            def provider(argv, deadline, emit):
                prompt = argv[argv.index("-p") + 1] if "-p" in argv else argv[-1]
                result_path = root / re.search(r"--result ([^ ]+)", prompt).group(1)
                progress_path = root / re.search(r"--checkpoint ([^ ]+)", prompt).group(1)
                if "--evaluate-only" in prompt:  # step one: fit evaluation only, nothing drafted yet
                    evaluation = {"score": 80, "verdict": "Strong Fit", "eligibility": "unverified", "recommendation": "Apply."}
                    result_path.write_text(json.dumps({"status": "evaluated", "evaluation": evaluation}))
                    observed_evaluation.append(prompt)
                    return {"limited": False, "provider_error": False, "returncode": 0}
                source = root / "cv/main_demo_engineer.tex"
                source.write_text("Source written by the simulated Claude run")
                for name in ("cv/main_demo_engineer.pdf", "cover_letters/cover_demo_engineer.pdf"):
                    (root / name).write_bytes(b"%PDF-1.4\n% controlled upload fixture\n%%EOF\n")
                result_path.write_text(json.dumps({"status": "drafted", "cv": "cv/main_demo_engineer.pdf", "cover": "cover_letters/cover_demo_engineer.pdf", "score": 80}))
                observed.append("Claude drafted and returned documents")
                return {"limited": False, "provider_error": False, "returncode": 0}

            @contextmanager
            def routed_browser():
                with sync_playwright() as pw:
                    def launch(*args, **kwargs):
                        context = pw.chromium.launch_persistent_context(*args, **kwargs)
                        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=FORM) if route.request.url == application else route.abort())
                        return context
                    yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

            def inspect(page):
                self.assertEqual(page.locator("#first").input_value(), "Demo Candidate")
                self.assertEqual(page.locator("#why").input_value(), "Demo answer for a controlled application test.")
                self.assertEqual(page.locator("#cv").evaluate("el => el.files[0].name"), "main_demo_engineer.pdf")
                self.assertEqual(page.locator("#cover").evaluate("el => el.files[0].name"), "cover_demo_engineer.pdf")
                self.assertEqual(page.locator("#visa").input_value(), "")
                self.assertFalse(page.locator("#consent").is_checked())
                self.assertIsNone(page.locator("body").get_attribute("data-submitted"))
                self.assertEqual(page.locator("#jd-live-status .status").inner_text(), "Ready for your review")
                observed.append("Chrome filled form and exact PDFs without submitting")
                print("DEMO: Form is ready for review. Chrome will stay visible for 30 seconds." if live else "Connected Chrome checks passed.", flush=True)

            original_fill = fill.run_fill

            def run_fill(*args, **kwargs):
                kwargs.update(wait_for_user_minutes=0.5 if live else 0, after_fill=inspect)
                return original_fill(*args, **kwargs)

            def fill_command(argv, **kwargs):
                self.assertEqual(argv[argv.index("--cv") + 1], str(root / "cv/main_demo_engineer.pdf"))
                self.assertEqual(argv[argv.index("--cover") + 1], str(root / "cover_letters/cover_demo_engineer.pdf"))
                cli_args = argv[1:] + ([] if live else ["--headless"])
                with patch.object(sys, "argv", cli_args):
                    return fill_form.main()

            stack.enter_context(patch.object(queue_runner, "_provider_attempt", side_effect=provider))
            stack.enter_context(patch.object(resolve, "resolve", return_value={"status": "found", "apply_url": application, "platform": "demo", "confidence": 1}))
            stack.enter_context(patch.object(draft, "draft_answers", return_value={"Why do you want this role?": "Demo answer for a controlled application test."}))
            stack.enter_context(patch("playwright.sync_api.sync_playwright", routed_browser))
            stack.enter_context(patch.object(fill, "run_fill", side_effect=run_fill))
            stack.enter_context(patch.object(apply_flow.subprocess, "call", side_effect=fill_command))
            stack.enter_context(patch.object(apply_flow, "ask_proceed", return_value=True))
            self.assertEqual(apply_flow.run(listing), 0)
            self.assertEqual(store.load_seen()["seen"][listing]["application_status"], "prepared")
            self.assertFalse((root / "tracker.csv").exists())
            report = json.loads(next((root / "documents/applications").glob("*/form_fill_report.json")).read_text())
            self.assertEqual(len(report["filled"]), 2)
            self.assertEqual(len(report["uploaded"]), 2)
            self.assertFalse(report["confirmed"])
            self.assertEqual(len(observed), 3)
            self.assertEqual(len(observed_evaluation), 1, "evaluation ran once, before drafting")
            print("PASS: " + " -> ".join(observed), flush=True)


if __name__ == "__main__":
    unittest.main()

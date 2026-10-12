"""Check live form filling against a local fixture, without sending an application."""

import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms import fill

FORM = b"""<!doctype html><html><head><title>Application preview</title>
<style>body{font:16px system-ui;background:#f5f6f3;color:#202c24;padding:30px 60px}
main{max-width:600px}label{display:block;margin:20px 0 6px}input,textarea{font:inherit;padding:10px;width:400px}
input[type=checkbox]{width:auto}button{margin-top:24px;padding:12px 30px}</style></head>
<body><main><h1>Engineer application</h1><p>Local test form</p>
<label for=first>First name</label><input id=first>
<label for=email>Email</label><input id=email type=email value=already@example.com>
<label for=why>Why do you want this role?</label><textarea id=why></textarea>
<label for=cv>Resume</label><input id=cv type=file>
<label for=cover>Cover letter</label><input id=cover type=file>
<label for=visa>Will you require visa sponsorship?</label><input id=visa>
<label><input id=consent type=checkbox>I agree to the privacy policy</label>
<button type=button onclick="document.body.dataset.submitted='true'">Submit application</button>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(FORM)

    def log_message(self, *args):
        pass


class LiveFormTests(unittest.TestCase):
    def test_visible_progress_fill_upload_and_review(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cv = root / "resume.pdf"
            cover = root / "cover.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            cover.write_bytes(b"%PDF-1.4 test")
            stages = []
            original_status = fill.LIVE_STATUS_JS

            def prepare(questions):
                self.assertEqual(questions, ["Why do you want this role?"])
                stages.append("prepared answers")
                return {questions[0]: "I enjoy building full-stack products."}

            def inspect(page):
                self.assertEqual(page.locator("#first").input_value(), "Jatin")
                self.assertEqual(page.locator("#email").input_value(), "already@example.com")
                self.assertEqual(page.locator("#why").input_value(), "I enjoy building full-stack products.")
                self.assertEqual(page.locator("#visa").input_value(), "")
                self.assertFalse(page.locator("#consent").is_checked())
                self.assertEqual(page.locator("#cv").evaluate("el => el.files[0].name"), "resume.pdf")
                self.assertEqual(page.locator("#cover").evaluate("el => el.files[0].name"), "cover.pdf")
                self.assertIsNone(page.locator("body").get_attribute("data-submitted"))
                self.assertEqual(page.locator("#jd-live-status .status").inner_text(), "Ready for your review")
                self.assertEqual(page.locator("[data-jd-live-active]").count(), 0)
                self.assertEqual(page.locator(".jd-flag").count(), 2)
                statuses = page.evaluate("window.liveStatusTest")
                self.assertIn("Drafting answers…", statuses)
                self.assertIn("Filling: First name", statuses)
                self.assertIn("Uploading: Resume", statuses)
                self.assertEqual(statuses[-1], "Ready for your review")
                stages.append("reviewed")

            screenshot_path = os.environ.get("JOBDESK_TEST_SCREENSHOT")
            status_probe = "(args) => { const update = " + original_status + "; update(args); (window.liveStatusTest ||= []).push(args.status); }"
            with patch.object(fill, "PROFILE_DIR", root / "profile"), patch.object(fill, "LIVE_STATUS_JS", status_probe):
                report = fill.run_fill(
                    f"http://127.0.0.1:{server.server_port}/",
                    {"first_name": "Jatin", "email": "new@example.com"},
                    {"cv": str(cv), "cover": str(cover)}, {}, lambda message: None,
                    headless=True, prepare_answers=prepare, after_fill=inspect,
                    screenshot=Path(screenshot_path) if screenshot_path else None,
                )
            self.assertEqual(stages, ["prepared answers", "reviewed"])
            self.assertEqual(len(report["filled"]), 2)
            self.assertEqual(len(report["uploaded"]), 2)
            self.assertFalse(report["confirmed"])


if __name__ == "__main__":
    unittest.main()

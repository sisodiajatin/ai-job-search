"""The page recorder describes structure only: never typed values, never passwords, never clicks."""

import functools
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms.engine import recorder

SECRET_TYPED = "SECRET-TYPED-VALUE-12345"
SECRET_PASSWORD = "hunter2-PASSWORD-xyz"

PAGE = f"""<!doctype html><html><head><title>Apply - Senior Widget Maker</title></head><body>
<h1>My Information</h1><h2>Step 1 of 4</h2>
<div aria-current="step">My Information</div>
<form>
  <label for="fn">Legal First Name *</label>
  <input id="fn" name="firstName" data-automation-id="legalNameSection_firstName" required value="{SECRET_TYPED}">
  <label for="em">Email</label><input id="em" type="email" value="jatinsisodiausa@gmail.com">
  <label for="pw">Password</label><input id="pw" type="password" value="{SECRET_PASSWORD}">
  <label for="st">State</label>
  <select id="st"><option>Select</option><option>Maryland</option><option>Massachusetts</option></select>
  <fieldset><legend>Are you over 18?</legend>
    <label><input type="radio" name="o18" value="y"> Yes</label>
    <label><input type="radio" name="o18" value="n"> No</label></fieldset>
  <label id="lbl-city">City</label>
  <button type="button" aria-haspopup="listbox" aria-labelledby="lbl-city">Choose</button>
  <label for="cv">Resume</label><input id="cv" type="file">
  <input type="text" aria-label="hidden-looking" style="display:none" value="NOT-VISIBLE-123">
  <iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>
  <button type="button" data-automation-id="bottom-navigation-next-button">Save and Continue</button>
  <button type="submit" disabled>Submit</button>
</form></body></html>"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass


class Server:
    """Tiny local web server so pages have a real host, like a job site."""

    def __init__(self, directory: Path):
        handler = functools.partial(_QuietHandler, directory=str(directory))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class RedactTests(unittest.TestCase):
    def test_profile_values_emails_and_numbers_are_masked(self):
        secrets = ["Jatin Sisodia", "jatinsisodiausa@gmail.com"]
        text = recorder.redact("Welcome Jatin Sisodia (jatinsisodiausa@gmail.com) call +1 774-701-4370 or other@x.org", secrets)
        self.assertEqual(text, "Welcome [me] ([me]) call [number] or [email]")

    def test_nested_structures_are_masked_and_non_strings_kept(self):
        data = {"a": ["me@x.org", {"b": "ok"}], "n": 3, "t": True}
        self.assertEqual(recorder.redact(data, []), {"a": ["[email]", {"b": "ok"}], "n": 3, "t": True})

    def test_short_or_private_keys_are_not_treated_as_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            path.write_text(json.dumps({"_comment": "ignore me please", "a": "ab", "name": "Jatin Sisodia"}), encoding="utf-8")
            self.assertEqual(recorder.load_secrets(path), ["Jatin Sisodia"])


class SnapshotAndObserveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "form.html").write_text(PAGE, encoding="utf-8")

    def test_snapshot_describes_structure_without_values_or_passwords(self):
        from playwright.sync_api import sync_playwright

        with Server(self.root) as base, sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.goto(f"{base}/form.html?token=SECRET-QUERY#frag")
                snap = recorder.snapshot(page, secrets=["jatinsisodiausa@gmail.com"])
            finally:
                browser.close()

        dumped = json.dumps(snap)
        for forbidden in (SECRET_TYPED, SECRET_PASSWORD, "NOT-VISIBLE-123", "SECRET-QUERY", "frag", "jatinsisodiausa@gmail.com"):
            self.assertNotIn(forbidden, dumped, forbidden)
        self.assertEqual(snap["path"], "/form.html")
        self.assertEqual(snap["title"], "Apply - Senior Widget Maker")
        self.assertIn("My Information", snap["headings"])
        self.assertTrue(snap["has_password"])
        self.assertTrue(snap["captcha"])
        self.assertTrue(all(f["type"] != "password" for f in snap["fields"]))
        by_label = {f["label"]: f for f in snap["fields"] if f["label"]}
        first = by_label["Legal First Name *"]
        self.assertEqual((first["required"], first["automation_id"], first["name"]), (True, "legalNameSection_firstName", "firstName"))
        self.assertEqual(by_label["State"]["options"], ["Select", "Maryland", "Massachusetts"])
        self.assertTrue(any(f["group"] == "Are you over 18?" and f["type"] == "radio" for f in snap["fields"]))
        self.assertTrue(any(f["popup"] == "listbox" and f["label"] == "City" for f in snap["fields"]))
        self.assertTrue(any(f["type"] == "file" for f in snap["fields"]))
        texts = {b["text"]: b for b in snap["buttons"]}
        self.assertEqual(texts["Save and Continue"]["automation_id"], "bottom-navigation-next-button")
        self.assertTrue(texts["Submit"]["disabled"])
        self.assertEqual(len(snap["signature"]), 12)

    def test_observe_writes_one_line_per_distinct_page_and_never_a_typed_value(self):
        changing = PAGE.replace("</body>", "<script>setTimeout(function(){var h=document.createElement('h1');"
                                           "h.textContent='My Experience';document.body.prepend(h);},1600);</script></body>")
        (self.root / "wizard.html").write_text(changing, encoding="utf-8")
        out = self.root / "observed"
        with Server(self.root) as base:
            result = recorder.observe(
                f"{base}/wizard.html", minutes=0.12, out_dir=out, log=lambda _m: None, headless=True,
                poll_seconds=0.4, profile_dir=self.root / "profile", show_banner=False,
            )
        lines = [json.loads(line) for file in out.glob("*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(result["pages"], 2, "the changed step must be recorded as a second page")
        self.assertEqual(len(lines), result["pages"])
        self.assertEqual(len({line["signature"] for line in lines}), len(lines), "no duplicate pages")
        blob = json.dumps(lines)
        for forbidden in (SECRET_TYPED, SECRET_PASSWORD, "NOT-VISIBLE-123"):
            self.assertNotIn(forbidden, blob)
        self.assertTrue(all(name.endswith(".jsonl") and ":" not in name for name in (p.name for p in out.iterdir())))

    def test_observe_command_refuses_non_https_urls_without_opening_a_browser(self):
        import fill_form

        with patch("apply_forms.engine.recorder.observe") as observe:
            self.assertEqual(fill_form.run_observe("http://example.com/job", 1), 1)
            self.assertEqual(fill_form.run_observe("https://example.com/a b", 1), 1)
        observe.assert_not_called()


if __name__ == "__main__":
    unittest.main()

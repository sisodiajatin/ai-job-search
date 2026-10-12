"""The form filler must wait for single-page apps to render, and must say so when a page has no form."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms import fill

SPA = """<!doctype html><html><body><div id="app">Loading...</div>
<script>
setTimeout(function () {
  document.getElementById('app').innerHTML =
    '<form><label for="fn">First Name</label><input id="fn" name="first_name">' +
    '<label for="em">Email</label><input id="em" type="email" name="email"></form>';
}, 2500);
</script></body></html>"""

JOB_PAGE = """<!doctype html><html><body><h1>Full Stack Developer</h1><p>Description of the role.</p>
<a href="#apply">Apply</a> <a href="#in">Sign In</a></body></html>"""


class FillWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile_dir = self.root / "profile"

    def page(self, html):
        path = self.root / "page.html"
        path.write_text(html, encoding="utf-8")
        return path.as_uri()

    def run_fill(self, html, **kwargs):
        logs = []
        original = fill.PROFILE_DIR
        fill.PROFILE_DIR = self.profile_dir
        try:
            report = fill.run_fill(
                self.page(html), {"first_name": "Jatin", "email": "me@example.com"}, {}, {}, logs.append,
                headless=True, **kwargs,
            )
        finally:
            fill.PROFILE_DIR = original
        return report, logs

    def test_a_form_that_renders_after_the_first_look_is_still_filled(self):
        report, _ = self.run_fill(SPA, field_wait_seconds=10)
        self.assertEqual(sorted(item["source"] for item in report["filled"]), ["email", "first_name"])
        self.assertNotIn("note", report)

    def test_a_page_with_no_form_fills_nothing_and_says_so(self):
        report, logs = self.run_fill(JOB_PAGE, field_wait_seconds=2)
        self.assertEqual(report["note"], "no_form_fields")
        self.assertEqual((report["filled"], report["uploaded"]), ([], []))
        self.assertTrue(any("No form fields found" in line for line in logs))
        self.assertFalse(any("Done filling" in line for line in logs), "must not claim it finished filling")

    def test_wait_for_fields_returns_quickly_when_the_form_is_already_there(self):
        import time
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.goto(self.page("<label for=a>Name</label><input id=a>"))
                started = time.time()
                fields = fill.wait_for_fields(page, max_seconds=10)
                self.assertEqual(len(fields), 1)
                self.assertLess(time.time() - started, 5)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()

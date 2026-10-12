"""Extension server side: token guard, plan preview, no sensitive fills; collect.js stays in sync."""

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from fastapi.testclient import TestClient

from api import ext
from api.app import app
import extension_build

PROFILE = {"first_name": "Jatin", "last_name": "Sisodia", "email": "j@example.com"}
FIELDS = [
    {"idx": 0, "label": "First name", "kind": "text", "type": "text", "value": ""},
    {"idx": 1, "label": "Email", "kind": "text", "type": "text", "value": ""},
    {"idx": 2, "label": "Will you now or in the future require sponsorship?", "kind": "text", "type": "text", "value": ""},
    {"idx": 3, "label": "Gender", "kind": "select", "type": "select", "options": ["Male", "Female"], "value": ""},
]


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "token"
        self.old = ext.TOKEN_PATH
        ext.TOKEN_PATH = self.path
        self.addCleanup(self._restore)
        self.token = ext.get_token(self.path)
        self.client = TestClient(app, base_url="http://127.0.0.1:8787")

    def _restore(self):
        ext.TOKEN_PATH = self.old

    def post(self, token=None, origin="chrome-extension://" + "a" * 32):
        headers = {"Origin": origin}
        if token:
            headers["X-Job-Desk-Token"] = token
        return self.client.post("/api/ext/plan", json={"fields": FIELDS}, headers=headers)

    def test_token_is_required_and_web_origins_are_refused(self):
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.post("wrong-token-wrong-token-wrong").status_code, 403)
        self.assertEqual(self.post(self.token, origin="https://evil.example").status_code, 403)

    def test_plan_fills_profile_fields_and_never_sensitive_ones(self):
        orig = ext.load_profile
        ext.load_profile = lambda path=ext.PROFILE_PATH: PROFILE
        self.addCleanup(lambda: setattr(ext, "load_profile", orig))
        res = self.post(self.token)
        self.assertEqual(res.status_code, 200)
        plan = res.json()
        self.assertEqual({f["label"]: f["value"] for f in plan["fill"]}, {"First name": "Jatin", "Email": "j@example.com"})
        never = {f["label"] for f in plan["never"]}
        self.assertIn("Gender", never)
        self.assertTrue(any("sponsorship" in label for label in never))
        self.assertFalse(any("sponsorship" in f["label"] or f["label"] == "Gender" for f in plan["fill"]))

    def test_token_endpoint_needs_the_dashboard_header(self):
        self.assertEqual(self.client.get("/api/ext/token").status_code, 403)
        ok = self.client.get("/api/ext/token", headers={"X-Job-Desk": "1"})
        self.assertEqual(ok.json()["token"], self.token)

    def test_collect_js_matches_the_engine(self):
        on_disk = (Path(extension_build.OUT)).read_text(encoding="utf-8")
        self.assertEqual(on_disk, extension_build.render(), "run: cd web && PYTHONPATH=. python extension_build.py")

    def test_collect_js_runs_in_a_page(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content('<label for=a>First name</label><input id=a><input type=password id=p>')
                page.evaluate(extension_build.OUT.read_text(encoding="utf-8"))
                fields = page.evaluate("window.__jdCollect()")
            finally:
                browser.close()
        self.assertEqual([f["label"] for f in fields], ["First name"])

    def _apply(self, html, profile, choose=None):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(html)
                page.evaluate(extension_build.OUT.read_text(encoding="utf-8"))
                fields = page.evaluate("window.__jdCollect()")
                plan = ext.build_plan(fields, profile)
                if choose:
                    plan["fill"] += [dict(f, value=choose[f["label"]]) for f in fields if f["label"] in choose]
                page.evaluate((extension_build.OUT.parent / "fill.js").read_text(encoding="utf-8") + "\n;undefined")
                results = page.evaluate("(plan) => window.__jdApply(plan)", plan)
                state = page.evaluate("""() => ({
                    first: document.getElementById('fn').value,
                    pw: document.getElementById('pw') ? document.getElementById('pw').value : null,
                    clicked: window.__clicked || [],
                    sel: document.getElementById('st').selectedOptions[0].text,
                    news: document.getElementById('news').checked,
                    outline: [...document.querySelectorAll('[data-jd-idx]')].filter(e => e.style.outline).length,
                })""")
            finally:
                browser.close()
        return plan, results, state

    PAGE = """<label for=fn>First name</label><input id=fn>
      <label for=st>Home state</label><select id=st><option>Select</option><option>MD</option></select>
      <label><input id=news type=checkbox> Newsletter</label>
      <label for=pw>Password</label><input id=pw type=password value=keep-me>
      <div role=group><span>Will you require visa sponsorship?</span>
        <button type=button onclick="(window.__clicked=window.__clicked||[]).push('yes')">Yes</button>
        <button type=button onclick="(window.__clicked=window.__clicked||[]).push('no')">No</button></div>
      <button type=submit onclick="(window.__clicked=window.__clicked||[]).push('SUBMIT')">Submit application</button>"""

    def test_apply_fills_with_readback_and_leaves_sensitive_and_password_alone(self):
        profile = {"first_name": "Jatin", "state": "MD"}
        plan, results, state = self._apply(self.PAGE, profile)
        self.assertEqual(state["first"], "Jatin")
        self.assertEqual(state["sel"], "MD")
        self.assertEqual(state["pw"], "keep-me")
        self.assertEqual(state["clicked"], [], "no yes/no and no submit click without a server decision")
        self.assertTrue(all(r["status"] == "filled" for r in results), results)
        self.assertGreaterEqual(state["outline"], 3)

    def test_apply_refuses_to_click_submit_even_if_told_to(self):
        plan = {"fill": [{"idx": 99, "label": "x", "kind": "buttons", "value": "Submit application"}]}
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(self.PAGE)
                page.evaluate("document.querySelector('button[type=submit]').parentElement.setAttribute('data-jd-idx','99')")
                page.evaluate((extension_build.OUT.parent / "fill.js").read_text(encoding="utf-8") + "\n;undefined")
                results = page.evaluate("(plan) => window.__jdApply(plan)", plan)
                clicked = page.evaluate("window.__clicked || []")
            finally:
                browser.close()
        self.assertEqual(clicked, [])
        self.assertNotEqual(results[0]["status"], "filled")

    def test_drafts_and_file_endpoints_need_the_token_and_serve_the_pdf(self):
        pdf = Path(self.temp.name) / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4 x")
        job = {"key": "k1", "company": "Acme", "title": "Dev", "cv_pdf": "cv/a.pdf", "cover_pdf": None}
        origin = {"Origin": "chrome-extension://" + "a" * 32}
        with patch("api.app.list_jobs", return_value=[job]), patch("api.app.resolve_allowed_file", return_value=pdf):
            self.assertEqual(self.client.get("/api/ext/drafts", headers=origin).status_code, 403)
            headers = {**origin, "X-Job-Desk-Token": self.token}
            self.assertEqual(self.client.get("/api/ext/drafts", headers=headers).json(), [{"key": "k1", "label": "Acme - Dev"}])
            self.assertEqual(self.client.get("/api/ext/file?key=k1&which=cv", headers=headers).content, b"%PDF-1.4 x")
            self.assertEqual(self.client.get("/api/ext/file?key=k1&which=cover", headers=headers).status_code, 404)

    def test_apply_attaches_pdf_to_the_resume_input_only(self):
        import base64
        from playwright.sync_api import sync_playwright

        html = "<div><label for=cv>Resume</label><input id=cv type=file></div><div><label for=o>Other file</label><input id=o type=file></div>"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(html)
                page.evaluate(extension_build.OUT.read_text(encoding="utf-8") + "\n;undefined")
                fields = page.evaluate("window.__jdCollect()")
                plan = ext.build_plan(fields, {})
                plan["files"] = {"cv": {"name": "cv.pdf", "b64": base64.b64encode(b"%PDF-1.4 x").decode()}}
                page.evaluate((extension_build.OUT.parent / "fill.js").read_text(encoding="utf-8") + "\n;undefined")
                results = page.evaluate("(plan) => window.__jdApply(plan)", plan)
                names = page.evaluate("[...document.querySelectorAll('input')].map(i => i.files[0] ? i.files[0].name : '')")
            finally:
                browser.close()
        self.assertEqual(names, ["cv.pdf", ""])
        self.assertEqual([r["status"] for r in results], ["filled"])


if __name__ == "__main__":
    unittest.main()

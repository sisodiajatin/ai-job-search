"""The stepper stops for the human, resumes when the page changes, and never presses Submit."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms.engine.hints import for_url, reload
from apply_forms.engine.stepper import pick_advance, run_on_page
from engine_http import FIXTURES

PROFILE = {"first_name": "Jatin", "email": "me@example.com", "city": "Columbia"}
HINTS = json.loads((Path(__file__).resolve().parents[1] / "web" / "apply_forms" / "engine" / "hints" / "workday.json").read_text(encoding="utf-8"))


class HintTests(unittest.TestCase):
    def test_workday_pack_matches_workday_hosts_only(self):
        reload()
        self.assertEqual(for_url("https://acme.wd1.myworkdayjobs.com/en-US/job/1")["id"], "workday")
        self.assertIsNone(for_url("https://notworkday.com/job"))
        self.assertIsNone(for_url("https://example.com/apply"))

    def test_a_denylisted_label_is_not_clickable_even_with_a_safe_id(self):
        buttons = [{"idx": 0, "text": "Submit", "automation_id": "applyManually", "disabled": False}]
        self.assertIsNone(pick_advance(buttons, final=False, hints=HINTS))
        self.assertIsNone(pick_advance([{"idx": 1, "text": "Next", "automation_id": "", "disabled": False}], final=True, hints={}))


class StepperTests(unittest.TestCase):
    def drive(self, name, check, **kwargs):
        from playwright.sync_api import sync_playwright

        report: dict = {}
        logs: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.goto((FIXTURES / name).as_uri())
                options = {"headless": True, "field_wait_seconds": 1, "resume_seconds": 0}
                options.update(kwargs)
                run_on_page(page, report, profile=PROFILE, files={}, answers={}, log=logs.append, **options)
                check(page, report, logs)
            finally:
                browser.close()
        return report

    def test_a_wizard_fills_both_steps_and_never_submits(self):
        def check(page, report, logs):
            self.assertEqual(page.locator("#em").input_value(), "me@example.com")
            self.assertEqual(page.locator("body").get_attribute("data-advanced"), "yes")
            self.assertIsNone(page.locator("body").get_attribute("data-submitted"))
            self.assertEqual(sorted(item["source"] for item in report["filled"]), ["email", "first_name"])
            self.assertNotIn("Submit", report["advanced"])

        self.drive("wizard.html", check)

    def test_login_otp_captcha_eeo_and_review_stop_without_typing_secrets(self):
        def login(page, report, logs):
            self.assertEqual(report["stop_reason"], "login")
            self.assertEqual(page.locator("#em").input_value(), "")
            self.assertNotIn("secret-value", json.dumps(report))

        def otp(page, report, logs):
            self.assertEqual(report["stop_reason"], "otp")
            self.assertEqual(page.locator("#code").input_value(), "")
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))

        def captcha(page, report, logs):
            self.assertEqual(report["stop_reason"], "captcha")
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))

        def eeo(page, report, logs):
            self.assertEqual(report["stop_reason"], "user_only")
            self.assertFalse(page.locator("input[type=radio]").first.is_checked())
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))

        def review(page, report, logs):
            self.assertEqual(report["stop_reason"], "review")
            self.assertIsNone(page.locator("body").get_attribute("data-submitted"))

        self.drive("login.html", login)
        self.drive("otp.html", otp)
        self.drive("captcha.html", captcha)
        self.drive("eeo.html", eeo)
        self.drive("review.html", review)

    def test_resume_after_login_when_the_page_changes(self):
        def on_stop(page, reason):
            self.assertEqual(reason, "login")
            page.set_content("<label for=fn>First name</label><input id=fn>", timeout=5000)

        def check(page, report, logs):
            self.assertEqual(page.locator("#fn").input_value(), "Jatin")
            self.assertNotEqual(report.get("stop_reason"), "login")

        self.drive("login.html", check, on_stop=on_stop, resume_seconds=3)

    def test_denylist_last_step_stuck_validation_and_unknown(self):
        def deny(page, report, logs):
            self.assertEqual(page.locator("#fn").input_value(), "Jatin")
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))
            self.assertTrue(report["advanced"] == [])

        def last(page, report, logs):
            self.assertEqual(report["stop_reason"], "review")
            self.assertEqual(page.locator("#fn").input_value(), "Jatin")
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))

        def stuck(page, report, logs):
            self.assertEqual(report["stop_reason"], "unknown")
            self.assertEqual(page.locator("body").get_attribute("data-clicks"), "1")

        def validation(page, report, logs):
            self.assertEqual(report["stop_reason"], "validation")
            self.assertEqual(page.locator("body").get_attribute("data-clicks"), "1")

        def unknown(page, report, logs):
            self.assertEqual(report["stop_reason"], "unknown")
            self.assertIsNone(page.locator("body").get_attribute("data-clicked"))

        self.drive("deny.html", deny)
        self.drive("last_step.html", last)
        self.drive("stuck.html", stuck)
        self.drive("validation.html", validation)
        self.drive("unknown.html", unknown)

    def test_workday_hint_clicks_apply_manually_only(self):
        def check(page, report, logs):
            self.assertEqual(page.locator("body").get_attribute("data-clicked"), "manual")
            self.assertEqual(page.locator("#fn").input_value(), "Jatin")

        self.drive("workday_gate.html", check, hints=HINTS)

    def test_a_fake_model_cannot_answer_sponsorship(self):
        def model(items):
            return {item["id"]: "Yes" for item in items}

        def check(page, report, logs):
            self.assertEqual(page.locator("#fn").input_value(), "Jatin")
            self.assertEqual(page.locator("[aria-pressed=true]").count(), 0)
            self.assertTrue(any("visa" in item["label"].lower() or "sponsor" in item["label"].lower() for item in report["needs_you"]))

        self.drive("sensitive.html", check, model=model, use_model=True)


if __name__ == "__main__":
    unittest.main()

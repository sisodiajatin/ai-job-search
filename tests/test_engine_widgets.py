"""Every widget kind can be set and read back. A miss returns could not set and does not raise."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms.engine import fields, widgets
from engine_http import FIXTURES

SECRET = "hunter2-PASSWORD-xyz"


class WidgetTests(unittest.TestCase):
    def test_each_kind_is_set_and_read_back(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory, sync_playwright() as pw:
            resume = Path(directory) / "resume.pdf"
            resume.write_bytes(b"%PDF-1.4 test")
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.goto((FIXTURES / "widgets.html").as_uri())
                collected = fields.collect(page)
                dumped = __import__("json").dumps(collected)
                self.assertNotIn(SECRET, dumped)
                self.assertNotIn("NOT-VISIBLE-123", dumped)
                self.assertFalse(any(item["type"] == "password" for item in collected))
                by_label = {}
                for item in collected:
                    by_label.setdefault(item["label"], item)

                self.assertEqual(by_label["Legal first name"]["kind"], "text")
                self.assertEqual(by_label["Why this role"]["kind"], "textarea")
                self.assertEqual(by_label["Home state"]["kind"], "select")
                self.assertEqual(by_label["Home state"]["options"], ["Select", "MD", "MA"])
                self.assertEqual(by_label["City"]["kind"], "combobox")
                self.assertEqual(by_label["City"]["options"], ["Boston", "Chicago"])
                self.assertEqual(by_label["Shift"]["kind"], "radio")
                self.assertEqual(by_label["Shift"]["options"], ["Day", "Night"])
                self.assertEqual(by_label["Desk"]["kind"], "radio")
                self.assertEqual(by_label["Weekend work"]["kind"], "buttons")
                self.assertEqual(by_label["Weekend work"]["options"], ["Yes", "No"])
                self.assertEqual(by_label["Overtime"]["kind"], "buttons")
                self.assertEqual(by_label["Newsletter"]["kind"], "checkbox")
                self.assertEqual(by_label["Remote ok"]["kind"], "checkbox")
                self.assertEqual(by_label["Graduation date"]["kind"], "date")
                self.assertEqual(by_label["Degree date"]["kind"], "date")
                self.assertEqual(by_label["Resume"]["kind"], "file")
                self.assertEqual(by_label["Resume upload"]["kind"], "file")
                self.assertEqual(by_label["Skills"]["kind"], "multiselect")

                checks = [
                    ("Legal first name", "Jatin", "Jatin"),
                    ("Why this role", "I build products.", "I build products."),
                    ("Home state", "MD", "MD"),
                    ("City", "Boston", "Boston"),
                    ("Shift", "Night", "Night"),
                    ("Desk", "Window", "Window"),
                    ("Weekend work", "Yes", "Yes"),
                    ("Overtime", "No", "No"),
                    ("Newsletter", "yes", "yes"),
                    ("Remote ok", "yes", "yes"),
                    ("Graduation date", "2016-05-01", "05/01/2016"),
                    ("Degree date", "2020-01-15", "2020-01-15"),
                    ("Skills", "Python", "Python"),
                ]
                for label, value, expected in checks:
                    with self.subTest(label=label):
                        self.assertIsNone(widgets.set_value(page, by_label[label], value))
                        self.assertEqual(widgets.read_value(page, by_label[label]), expected)
                self.assertIsNone(widgets.set_file(page, by_label["Resume"], str(resume)))
                self.assertEqual(page.locator("#cv").evaluate("el => el.files[0].name"), "resume.pdf")
                self.assertEqual(widgets.set_value(page, by_label["Home state"], "Texas"), "could not set")
                self.assertEqual(widgets.read_value(page, by_label["Home state"]), "MD")
                self.assertEqual(widgets.set_value(page, {"idx": 999, "kind": "text", "label": "Missing"}, "x"), "could not set")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()

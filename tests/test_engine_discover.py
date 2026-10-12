"""Apply-link discovery: board lookup, then an Apply control, else a paste-the-link message."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from apply_forms.engine.discover import find_apply_link
from engine_http import Server


class DiscoverTests(unittest.TestCase):
    def test_a_board_match_is_the_fast_path_and_does_not_open_a_browser(self):
        with patch("apply_forms.engine.discover.resolve", return_value={"status": "found", "apply_url": "https://boards.example/apply", "platform": "greenhouse"}) as resolve:
            found = find_apply_link("https://example.com/job", company="Acme", title="Engineer")
        self.assertEqual(found["apply_url"], "https://boards.example/apply")
        self.assertEqual(found["via"], "greenhouse")
        resolve.assert_called_once()

    def test_listing_popup_and_missing_apply_control(self):
        with tempfile.TemporaryDirectory() as directory, Server() as base:
            profile = Path(directory) / "profile"

            linked = find_apply_link(f"{base}/listing.html", headless=True, profile_dir=profile)
            self.assertEqual(linked["status"], "found")
            self.assertTrue(linked["apply_url"].endswith("/apply_target.html"), linked)
            self.assertEqual(linked["via"], "listing")

            popup = find_apply_link(f"{base}/popup_listing.html", headless=True, profile_dir=profile)
            self.assertEqual(popup["status"], "found", popup)
            self.assertTrue(popup["apply_url"].endswith("/apply_target.html"), popup)
            self.assertEqual(popup["via"], "popup")

            missing = find_apply_link(f"{base}/no_apply.html", headless=True, profile_dir=profile)
            self.assertEqual(missing["status"], "needs_link")
            self.assertIn("Paste the application link", missing["message"])
            self.assertFalse(missing.get("apply_url"))


if __name__ == "__main__":
    unittest.main()

"""Telegram approval: how replies like "drop 3 5" are understood."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from telegram_approval import parse_reply


class ParseReplyTests(unittest.TestCase):
    def test_replies(self):
        self.assertEqual(parse_reply("drop 3 5", 6), {3, 5})
        self.assertEqual(parse_reply("Drop 2, 4 and 9", 5), {2, 4})  # 9 is out of range
        self.assertEqual(parse_reply("ok", 4), set())
        self.assertEqual(parse_reply("none", 3), {1, 2, 3})
        self.assertIsNone(parse_reply("thanks!", 3))


if __name__ == "__main__":
    unittest.main()

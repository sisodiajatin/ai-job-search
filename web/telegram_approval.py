"""Telegram approval gate: send today's queue, wait for "drop 3 5" / "ok" / "none", remove what you dropped.

Uses its OWN bot (not the Hermes one, whose gateway already reads that bot's messages). No AI model involved: replies are parsed with a regex.
Setup once: create a bot with @BotFather, message it /start, then save
    %LOCALAPPDATA%\\jobdesk\\telegram.json   {"token": "<bot token>", "chat_id": "<your Telegram user id>"}

Usage (repository root):
    python web/telegram_approval.py --wait 30     # send the queue, wait up to 30 minutes for a reply, then drop what you asked
Exit code 0 always when unconfigured or the queue is empty (nothing to approve); no reply within the wait = keep everything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "jobdesk" / "telegram.json"
POLL_SECONDS = 10


def parse_reply(text: str, size: int) -> set[int] | None:
    """1-based numbers to drop, or None when the message is not an answer. "ok"/"go" = drop nothing, "none" = drop all."""
    words = text.strip().lower()
    if re.fullmatch(r"(ok|okay|go|yes|approve|all)\W*", words):
        return set()
    if re.fullmatch(r"(none|cancel|stop|skip all)\W*", words):
        return set(range(1, size + 1))
    match = re.match(r"(drop|skip|remove|no)\b(.*)", words)
    if match:
        return {int(n) for n in re.findall(r"\d+", match.group(2)) if 1 <= int(n) <= size}
    return None


def call(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, urllib.parse.urlencode(params).encode()), timeout=40) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:  # Telegram explains itself in the body ("chat not found" = send the bot /start first)
        raise SystemExit(f"Telegram approval failed: {json.load(err).get('description', err)}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wait", type=int, default=30, help="minutes to wait for your reply before keeping everything")
    args = parser.parse_args(argv)
    if not CONFIG.is_file():
        print(f"Telegram approval not set up ({CONFIG} missing); drafting everything queued.")
        return 0
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    token, chat = cfg["token"], str(cfg["chat_id"])

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    from api.store import dequeue_job, list_queue

    queue = list_queue()
    if not queue:
        return 0
    lines = [f"{i}. {q.get('company') or '?'} | {q.get('title') or ''}" for i, q in enumerate(queue, 1)]
    stale = call(token, "getUpdates", timeout=0).get("result", [])
    offset = stale[-1]["update_id"] + 1 if stale else 0  # ignore anything sent before this list
    call(token, "sendMessage", chat_id=chat, text=f"{len(queue)} jobs queued for drafting:\n" + "\n".join(lines)
         + f"\n\nReply 'drop 3 5' to remove jobs, 'none' to cancel all, or 'ok'. No reply in {args.wait} min = draft all.")

    deadline = time.monotonic() + args.wait * 60
    while time.monotonic() < deadline:
        for update in call(token, "getUpdates", offset=offset, timeout=POLL_SECONDS).get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if str(message.get("chat", {}).get("id")) != chat:
                continue
            drop = parse_reply(message.get("text") or "", len(queue))
            if drop is None:
                continue
            for number in drop:
                dequeue_job(queue[number - 1]["url"])
            kept = len(queue) - len(drop)
            call(token, "sendMessage", chat_id=chat, text=f"Dropped {len(drop)}, drafting {kept}." if kept else "Cancelled: nothing will be drafted.")
            print(f"Telegram approval: dropped {len(drop)}, keeping {kept}.")
            return 0
    print("Telegram approval: no reply, drafting everything queued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

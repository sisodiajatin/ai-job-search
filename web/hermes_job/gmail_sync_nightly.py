"""Scheduled Gmail-sync check for a Hermes Agent cron job running in `--no-agent` mode.

Unlike nightly_queue.py (drafting; no model judgment needed to run headless), /gmail-sync can
find something that genuinely needs a human decision (a proposed tracker status change). This
script runs /gmail-sync headlessly via `claude -p` and prints its own output verbatim, so Hermes
delivers the proposed-changes table (or "nothing to propose") to the user. Nothing is written to
the tracker or outcome.md here or inside /gmail-sync until the user replies to approve - this
script only reads Gmail and reports.

Override the claude binary with JOBDESK_CLAUDE_BIN if it is not on PATH for the scheduler.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_TIMEOUT_SECONDS = 10 * 60

ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "mcp__claude_ai_Gmail__list_labels",
        "mcp__claude_ai_Gmail__search_threads",
        "mcp__claude_ai_Gmail__get_thread",
        "mcp__claude_ai_Gmail__get_message",
    ]
)


def find_claude() -> str | None:
    return os.environ.get("JOBDESK_CLAUDE_BIN") or shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude") if p.is_file()), None
    )


def main() -> int:
    claude = find_claude()
    if not claude:
        print("Gmail sync could not start: `claude` is not on PATH.", file=sys.stderr)
        return 1
    try:
        proc = subprocess.run(
            [claude, "-p", "/gmail-sync", "--allowedTools", ALLOWED_TOOLS, "--permission-mode", "acceptEdits"],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=RUN_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        print("Gmail sync did not finish within 10 minutes and was stopped.", file=sys.stderr)
        return 1
    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        print(f"Gmail sync ended with exit code {proc.returncode}.\n{(proc.stderr or '')[-2000:]}", file=sys.stderr)
        return 1
    print(output or "(no output)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

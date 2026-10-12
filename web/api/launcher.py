"""Open a terminal window running the draft-to-form flow for a listing.

Safety notes:
- The URL never goes into a command string. It is passed through an environment variable and
  expanded by PowerShell as a variable, so it is not re-parsed as code.
- The URL must also match a conservative https-only pattern (no quotes, semicolons, backticks,
  spaces, or `$`), because listing URLs come from scraped, untrusted pages.
- No shell is used to start the process (argument list, no `shell=True`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Any

from .paths import REPO

ENV_VAR = "JOBDESK_APPLY_URL"
_SAFE_URL = re.compile(r"^https://[A-Za-z0-9._~:/?#@%&=+\-]{1,2000}$")
_PS_COMMAND = '& $env:JOBDESK_PYTHON $env:JOBDESK_APPLY_SCRIPT $env:JOBDESK_APPLY_URL'


class LaunchError(Exception):
    """Raised when the terminal cannot be opened; the message is safe to show the user."""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


def validate_url(url: str) -> str:
    if not _SAFE_URL.match(url or ""):
        raise LaunchError("This listing URL has characters I will not pass to a terminal. Use /apply in chat instead.", 400)
    return url


def build_launch(url: str) -> tuple[list[str], dict[str, str]]:
    """Return (argv, env) for the terminal process. Pure function, used by the tests."""
    url = validate_url(url)
    argv = ["powershell", "-NoExit", "-Command", _PS_COMMAND]
    env = {**os.environ, ENV_VAR: url, "JOBDESK_PYTHON": sys.executable,
           "JOBDESK_APPLY_SCRIPT": str(REPO / "web" / "apply_flow.py")}
    return argv, env


def launch_apply(url: str) -> dict[str, Any]:
    if sys.platform != "win32":
        raise LaunchError("Opening a terminal from the dashboard is only set up for Windows. Run /apply in chat instead.", 501)
    if shutil.which("claude") is None:
        raise LaunchError("The `claude` command was not found on PATH. Install Claude Code or run /apply in chat.", 500)
    argv, env = build_launch(url)
    subprocess.Popen(  # noqa: S603 - argv list, validated URL, no shell
        argv,
        cwd=str(REPO),
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return {"launched": True, "command": "python web/apply_flow.py <listing url>"}


def launch_fill(url: str) -> dict[str, Any]:
    """Open a console running web/fill_form.py for a listing. The script opens Chrome and never submits."""
    if sys.platform != "win32":
        raise LaunchError("Opening a terminal from the dashboard is only set up for Windows. Run python web/fill_form.py instead.", 501)
    url = validate_url(url)
    argv = [sys.executable, str(REPO / "web" / "fill_form.py"), url, "--draft"]
    subprocess.Popen(  # noqa: S603 - argv list, validated URL, no shell
        argv,
        cwd=str(REPO),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return {"launched": True, "command": "python web/fill_form.py <listing url> --draft"}

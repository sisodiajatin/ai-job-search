"""Recognise when Claude reports a usage or rate limit, so a run can stop with a clear message.

Only errors the CLI itself reports count. Budget caps are intentional and are never treated as a
usage limit, and tool output, posting text and normal assistant prose are never scanned.
"""

from __future__ import annotations

import json
import re
from typing import Any


def is_limit_message(message: str) -> bool:
    # Spending caps are intentional; they are a different failure from a usage limit.
    if re.search(r"budget|max[_ -]?budget|spending cap", message, re.I):
        return False
    return bool(re.search(
        r"rate[_ -]?limit|usage[_ -]?limit|quota[_ -]?(?:exceeded|exhausted)|"
        r"insufficient_quota|too many requests|you(?:'|’)?ve hit your limit|"
        r"(?:weekly|daily|session|message|token) limit (?:reached|exceeded)|"
        r"(?:hit|reached|exceeded) (?:your |the )?(?:usage |rate )?limit|\b429\b",
        message, re.I,
    ))


def limit_event(event: dict[str, Any]) -> bool:
    kind = event.get("type")
    # Never scan tool results, JD text or normal assistant prose for limits.
    if kind == "assistant":
        error = event.get("error")
    elif kind in ("error", "turn.failed"):
        error = event.get("error") or event.get("message")
    elif kind == "result" and event.get("is_error"):
        error = event.get("errors") or event.get("result") or event.get("subtype")
    else:
        return False
    return bool(error) and is_limit_message(json.dumps(error, ensure_ascii=False))


def plain_limit_error(line: str) -> bool:
    # Non-JSON CLI stderr: accept recognizable error prefixes only.
    return bool(re.match(r"^(?:error\b|api error\b|you(?:'|’)?ve hit your limit)", line, re.I)) and is_limit_message(line)

"""Optional per-site hints. The generic engine runs with none of these loaded."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

HINTS_DIR = Path(__file__).resolve().parent / "hints"


def _host_matches(host: str, suffix: str) -> bool:
    host = (host or "").lower().split(":")[0]
    suffix = suffix.lower()
    return host == suffix or host.endswith("." + suffix)


@lru_cache(maxsize=1)
def _packs() -> tuple[dict, ...]:
    packs = []
    if not HINTS_DIR.is_dir():
        return ()
    for path in sorted(HINTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            packs.append(data)
    return tuple(packs)


def for_url(url: str) -> dict | None:
    """Return the hint pack whose hosts match ``url``, or None."""
    host = urlsplit(url or "").netloc
    for pack in _packs():
        if any(_host_matches(host, suffix) for suffix in pack.get("hosts") or []):
            return pack
    return None


def reload() -> None:
    _packs.cache_clear()

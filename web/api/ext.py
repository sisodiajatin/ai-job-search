"""Server side of the browser extension: plan only, never fills. Profile data stays on this machine.

The extension sends the fields it collected from the page. The server answers with what it would
put in each field, using the same rules as the Playwright filler (sensitive questions are never
filled). Calls need the local token in `X-Job-Desk-Token`.
"""

from __future__ import annotations

import hmac
import json
import secrets
from pathlib import Path
from typing import Any

from apply_forms.engine.mapper import map_fields

WEB_DIR = Path(__file__).resolve().parents[1]
TOKEN_PATH = WEB_DIR / ".ext-token"
PROFILE_PATH = WEB_DIR / "apply_forms" / "profile_fields.json"
MAX_FIELDS = 400


def get_token(path: Path | None = None) -> str:
    """Return the extension token, creating it on first use."""
    path = path or TOKEN_PATH
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def token_ok(given: str | None, path: Path | None = None) -> bool:
    return bool(given) and hmac.compare_digest(given.strip(), get_token(path))


def load_profile(path: Path = PROFILE_PATH) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: str(v) for k, v in data.items() if not k.startswith("_")}


def _brief(field: dict[str, Any]) -> dict[str, Any]:
    return {"idx": field.get("idx"), "label": field.get("label") or "", "kind": field.get("kind") or field.get("type") or ""}


def build_plan(fields: list[Any], profile: dict[str, str] | None = None) -> dict[str, Any]:
    """Map collected fields to a preview plan. No model call, no label-memory writes."""
    clean = [f for f in fields[:MAX_FIELDS] if isinstance(f, dict) and f.get("idx") is not None]
    plan = map_fields(clean, load_profile() if profile is None else profile, {}, remember=False)
    return {
        "fill": [{**_brief(f), "value": f.get("value_to_fill", ""), "source": f.get("source", "")} for f in plan["fill"]],
        "never": [_brief(f) for f in plan["never"]],
        "upload": [{**_brief(f), "which": f.get("which", "")} for f in plan["upload"]],
        "left_for_you": [_brief(f) for f in plan["choice"] + plan["questions"]],
        "already_filled": [_brief(f) for f in plan["skipped_filled"]],
    }

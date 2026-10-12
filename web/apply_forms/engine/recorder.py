"""Passive page-structure recorder (`fill_form.py --observe`).

You apply by hand in the filler's Chrome window; this module watches and writes one JSON line per
distinct page to `web/apply_forms/observed/<host>.jsonl`. Those recordings are how new site support
is built from real pages instead of guesses.

What it records: URL path (no query or fragment), page title, headings, field labels, kinds,
required flags, `<select>` option texts, radio/checkbox group options, button texts and the
`data-automation-id` / `name` / `id` hints.

What it never does: click, type, submit, read any field value, or describe a password field (it only
notes that one exists). Everything recorded is also passed through `redact`, which masks your own
profile values, email addresses and phone-like numbers.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

APPLY_FORMS_DIR = Path(__file__).resolve().parents[1]
OBSERVED_DIR = APPLY_FORMS_DIR / "observed"
PROFILE_FIELDS = APPLY_FORMS_DIR / "profile_fields.json"

# Evaluated in the page. Reads structure only: never `.value`, never password fields, never clicks.
RECORD_JS = r"""
() => {
  const clean = (t, n = 200) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || (el.offsetParent === null && s.position !== 'fixed'));
  };
  const labelOf = (el) => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(' ').map((x) => (document.getElementById(x) || {}).innerText || '').join(' ');
    }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    return clean(t);
  };
  const groupOf = (el) => {
    const fs = el.closest('fieldset, [role=radiogroup], [role=group]');
    if (!fs) return '';
    const lg = fs.querySelector('legend') || (fs.getAttribute('aria-labelledby') && document.getElementById(fs.getAttribute('aria-labelledby').split(' ')[0]));
    return clean(lg ? lg.innerText : fs.getAttribute('aria-label') || '');
  };
  const fields = [];
  let hasPassword = false;
  const sel = 'input, textarea, select, [role=combobox], [role=listbox], [role=radiogroup], [role=radio], [role=checkbox], [role=switch], button[aria-haspopup]';
  for (const el of document.querySelectorAll(sel)) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || tag).toLowerCase();
    if (type === 'password') { hasPassword = true; continue; }
    if (tag === 'input' && ['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    if (type !== 'file' && !visible(el)) continue;
    const role = el.getAttribute('role') || '';
    const popup = el.getAttribute('aria-haspopup') || '';
    let kind = 'text';
    if (type === 'file') kind = 'file';
    else if (type === 'radio' || role === 'radio') kind = 'radio';
    else if (type === 'checkbox' || role === 'checkbox') kind = 'checkbox';
    else if (tag === 'select') kind = 'select';
    else if (tag === 'textarea') kind = 'textarea';
    else if (role === 'combobox' || popup === 'listbox') kind = 'combobox';
    const entry = {
      tag, type, kind, role, popup,
      label: labelOf(el), group: groupOf(el),
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      automation_id: el.getAttribute('data-automation-id') || '', name: el.getAttribute('name') || '', id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
    };
    if (tag === 'select') entry.options = [...el.options].slice(0, 80).map((o) => clean(o.text, 80));
    fields.push(entry);
  }
  const buttons = [];
  for (const el of document.querySelectorAll('button, [role=button], input[type=submit], input[type=button], a[role=button]')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.getAttribute('value') || el.getAttribute('aria-label'), 80);
    if (!text && !el.getAttribute('data-automation-id')) continue;
    buttons.push({ text, automation_id: el.getAttribute('data-automation-id') || '', type: el.getAttribute('type') || '', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true' });
  }
  const headings = [...document.querySelectorAll('h1, h2, h3')].filter(visible).slice(0, 8).map((h) => clean(h.innerText, 120)).filter(Boolean);
  const steps = [...document.querySelectorAll('[aria-current=step], [aria-current=page], [data-automation-id*=progress]')].slice(0, 4).map((e) => clean(e.innerText, 80)).filter(Boolean);
  const captcha = [...document.querySelectorAll('iframe')].some((f) => /recaptcha|hcaptcha|turnstile|captcha/i.test(f.src || '')) ||
    !!document.querySelector('[class*=captcha i], [id*=captcha i], .g-recaptcha, .h-captcha, .cf-turnstile');
  return { title: clean(document.title, 120), headings, steps, fields, buttons, has_password: hasPassword, captcha };
}
"""

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")


def load_secrets(path: Path = PROFILE_FIELDS) -> list[str]:
    """Your own profile values (name, email, phone, links...), longest first, so they can be masked."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values = {str(v).strip() for k, v in data.items() if not k.startswith("_") and len(str(v).strip()) >= 3}
    return sorted(values, key=len, reverse=True)


def redact(value: Any, secrets: list[str]) -> Any:
    """Mask your profile values, emails and phone-like numbers in every string of a nested structure."""
    if isinstance(value, str):
        text = value
        for secret in secrets:
            text = re.sub(re.escape(secret), "[me]", text, flags=re.I)
        return _PHONE.sub("[number]", _EMAIL.sub("[email]", text))
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    return value


def signature(snap: dict[str, Any]) -> str:
    parts = [snap.get("host", ""), snap.get("path", ""), "|".join(sorted(f.get("label", "") for f in snap.get("fields", []))),
             "|".join(b.get("text", "") for b in snap.get("buttons", [])), "|".join(snap.get("headings", []))]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def snapshot(page: Any, secrets: list[str] | None = None) -> dict[str, Any]:
    """Describe the page's structure. Safe to call repeatedly; changes nothing on the page."""
    raw = page.evaluate(RECORD_JS)
    parts = urlsplit(page.url)
    snap = {"host": parts.netloc.lower(), "path": parts.path, **raw}
    snap = redact(snap, secrets if secrets is not None else load_secrets())
    snap["signature"] = signature(snap)
    return snap


def observe(
    url: str,
    *,
    minutes: float = 90.0,
    out_dir: Path | None = None,
    log: Callable[[str], None] = print,
    headless: bool = False,
    poll_seconds: float = 1.5,
    profile_dir: Path | None = None,
    show_banner: bool = True,
) -> dict[str, Any]:
    """Open `url` in the filler's Chrome profile and record each distinct page until the window closes."""
    from playwright.sync_api import sync_playwright

    from ..fill import LIVE_STATUS_JS, PROFILE_DIR

    out_dir = Path(out_dir) if out_dir else OBSERVED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    secrets = load_secrets()
    seen: set[tuple[str, str]] = set()
    files: set[str] = set()
    pages_recorded = 0

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir or PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport=None if not headless else {"width": 1280, "height": 1600},
            args=["--start-maximized"] if not headless else [],
        )
        try:
            first = context.pages[0] if context.pages else context.new_page()
            log(f"Opening {urlsplit(url).netloc}{urlsplit(url).path} - apply by hand; structure only is recorded.")
            first.goto(url, wait_until="domcontentloaded", timeout=45000)
            deadline = time.time() + minutes * 60
            while time.time() < deadline:
                open_pages = [p for p in context.pages if not p.is_closed()]
                if not open_pages:
                    break
                for page in open_pages:
                    try:
                        snap = snapshot(page, secrets)
                    except Exception:  # noqa: BLE001 - page navigating or closing; try again next poll
                        continue
                    if not snap["host"] or page.url.startswith(("about:", "chrome:")):
                        continue
                    key = (snap["host"], snap["signature"])
                    if key in seen:
                        continue
                    seen.add(key)
                    snap["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    target = out_dir / (re.sub(r"[^a-z0-9.\-]", "_", snap["host"]) + ".jsonl")
                    with target.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(snap, ensure_ascii=False) + "\n")
                    files.add(str(target))
                    pages_recorded += 1
                    log(f"  recorded page {pages_recorded}: {snap['path'] or '/'}  ({len(snap['fields'])} fields, {len(snap['buttons'])} buttons)")
                    if show_banner and not headless:
                        try:
                            page.evaluate(LIVE_STATUS_JS, {"status": "Recording page structure", "detail": "Labels and button names only. Nothing you type is saved."})
                        except Exception:  # noqa: BLE001
                            pass
                time.sleep(poll_seconds)
        finally:
            context.close()
    return {"pages": pages_recorded, "files": sorted(files)}

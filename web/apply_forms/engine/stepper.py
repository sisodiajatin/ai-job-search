"""Walk a multi-step application, filling only what is safe, and stop for the user.

Clicks come from a short allowlist. Submit, send, finish, confirm, place and "complete
application" are never clicked, and neither is the last step's primary button. Account
creation, email codes, CAPTCHAs and voluntary-disclosure pages are a stop: the window
stays open and the walk resumes when the page changes.
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

from ..classify import classify
from .fields import collect
from .hints import for_url
from .mapper import map_fields
from .widgets import set_file, set_value

ALLOW = ("save and continue", "apply manually", "start application", "continue", "next")
_DENY = (
    re.compile(r"submit", re.I),
    re.compile(r"\bsend\b", re.I),
    re.compile(r"\bfinish\b", re.I),
    re.compile(r"\bconfirm\b", re.I),
    re.compile(r"\bplace\b", re.I),
    re.compile(r"complete application", re.I),
)
_OTP = re.compile(r"verify your email|verification code|one-time (password|code)|enter the code|security code", re.I)
_USER = re.compile(r"voluntary|self-identif|disability|veteran|equal employment|\beeo\b", re.I)
_REVIEW = re.compile(r"review your application|review and submit|submit your application", re.I)
_LISTING = re.compile(r"\b(apply|sign in|log in|job description)\b", re.I)
_PAUSE = {"login", "otp", "captcha", "user_only", "validation", "unknown"}
_NEVER_WHY = "work authorization, sponsorship, demographic, salary, start date or consent: only you should answer"
_NO_FORM = "This looks like a job description or sign-in page. Nothing was filled. Click Apply or sign in yourself, then run the filler again on the form page."
_BANNERS = {
    "login": ("Sign in or create your account", "This window stays open. The filler continues when the page changes. It never types a password."),
    "otp": ("Enter the verification code", "This window stays open. The filler continues when the page changes."),
    "captcha": ("Complete the CAPTCHA", "This window stays open. The filler continues when the page changes."),
    "user_only": ("This page is yours to answer", "Disclosures, self-identification, disability, veteran, signature or consent. The filler continues when the page changes."),
    "review": ("Review page", "Nothing was submitted. Check the application and submit it yourself."),
    "validation": ("The page has errors", "Fix them yourself. The filler continues when the page changes."),
    "unknown": ("This page is not one the filler understands", "Continue yourself. The filler resumes when the page changes."),
}

_PROBE_JS = r"""
() => {
  const clean = (t, n = 160) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || (el.offsetParent === null && s.position !== 'fixed'));
  };
  const headings = [...document.querySelectorAll('h1, h2, h3')].filter(visible).slice(0, 8).map((el) => clean(el.innerText)).filter(Boolean);
  const bits = [...document.querySelectorAll('a, button, [role=button]')].filter(visible).slice(0, 30).map((el) => clean(el.innerText || el.getAttribute('aria-label') || '', 80)).filter(Boolean);
  const captcha = [...document.querySelectorAll('iframe')].some((frame) => /recaptcha|hcaptcha|turnstile|captcha/i.test(frame.src || ''))
    || !!document.querySelector('[class*=captcha i], [id*=captcha i], .g-recaptcha, .h-captcha, .cf-turnstile');
  const alertEl = [...document.querySelectorAll('[role=alert]')].find((el) => !el.hidden && clean(el.innerText) && getComputedStyle(el).display !== 'none');
  return {
    password: !!document.querySelector('input[type=password]'),
    captcha, title: clean(document.title, 160), headings, bits,
    alert: !!alertEl || !!document.querySelector('[aria-invalid=true]'),
  };
}
"""

_BUTTONS_JS = r"""
() => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const visible = (el) => {
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || (el.offsetParent === null && s.position !== 'fixed'));
  };
  for (const old of document.querySelectorAll('[data-jd-btn]')) old.removeAttribute('data-jd-btn');
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.getAttribute('value') || el.getAttribute('aria-label') || '');
    if (!text && !el.getAttribute('data-automation-id')) continue;
    el.setAttribute('data-jd-btn', String(i));
    out.push({
      idx: i, text, type: (el.getAttribute('type') || '').toLowerCase(),
      automation_id: el.getAttribute('data-automation-id') || '',
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    });
    i += 1;
  }
  return out;
}
"""


def _norm(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip().lower()
    return re.sub(r"[\s>*›»→]+$", "", collapsed).strip()


def denied(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _DENY)


def is_final_step(text: str) -> bool:
    if _REVIEW.search(text or ""):
        return True
    match = re.search(r"step\s+(\d+)\s+of\s+(\d+)", text or "", re.I)
    return bool(match and match.group(1) == match.group(2) and int(match.group(1)) > 0)


def pick_advance(buttons: list[dict[str, Any]], *, final: bool, hints: dict | None = None) -> dict[str, Any] | None:
    """The one button this step may click, or None. Denylist and the last step always win."""
    if final:
        return None
    hints = hints or {}
    never_ids = set(hints.get("never_automation_ids") or [])
    safe_ids = set(hints.get("safe_automation_ids") or [])
    usable = [
        button for button in buttons
        if not button.get("disabled") and button.get("automation_id") not in never_ids and not denied(button.get("text") or "")
    ]
    for button in usable:
        if button.get("automation_id") in safe_ids:
            return button
    for phrase in ALLOW:
        matches = [button for button in usable if _norm(button.get("text") or "") == phrase]
        if matches:
            return matches[-1]
    return None


def _blob(probe: dict[str, Any]) -> str:
    parts = [probe.get("title") or ""]
    parts.extend(probe.get("headings") or [])
    parts.extend(probe.get("bits") or [])
    return " ".join(parts)


def _signature(page: Any, fields: list[dict[str, Any]], buttons: list[dict[str, Any]], probe: dict[str, Any]) -> str:
    payload = {
        "path": urlsplit(page.url).path,
        "labels": sorted((field.get("label") or "") for field in fields),
        "buttons": [button.get("text") or "" for button in buttons],
        "captcha": bool(probe.get("captcha")),
        "password": bool(probe.get("password")),
        "title": probe.get("title") or "",
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _user_only(fields: list[dict[str, Any]], text: str) -> bool:
    if _USER.search(text or ""):
        return True
    return bool(fields) and all(classify(field) == "never" for field in fields)


def _early_reason(probe: dict[str, Any], fields: list[dict[str, Any]], buttons: list[dict[str, Any]], hints: dict) -> str | None:
    text = _blob(probe)
    if probe.get("password"):
        return "login"
    if _OTP.search(text):
        return "otp"
    if _user_only(fields, text):
        return "user_only"
    if probe.get("captcha") and not fields:
        return "captcha"
    if is_final_step(text) and not fields:
        return "review"
    if not fields:
        if pick_advance(buttons, final=False, hints=hints):
            return None
        if _LISTING.search(text) or not buttons:
            return "no_form"
        return "unknown"
    return None


def run_on_page(
    page: Any,
    report: dict[str, Any],
    *,
    profile: dict[str, str],
    files: dict[str, str | None],
    answers: dict[str, str],
    log: Callable[[str], None],
    prepare_answers: Callable[[list[str]], dict[str, str]] | None = None,
    live_delay_ms: int = 650,
    field_wait_seconds: float = 15.0,
    headless: bool = False,
    max_steps: int = 8,
    resume_seconds: float = 0.0,
    on_stop: Callable[[Any, str], None] | None = None,
    hints: dict | None = None,
    memory_path: Path | None = None,
    model: Callable[..., Any] | None = None,
    use_model: bool = False,
    screenshot: Path | None = None,
) -> dict[str, Any]:
    """Fill ``page`` in place. Mutates and returns ``report``."""
    from .. import fill as fillmod

    for key, value in (("filled", []), ("uploaded", []), ("needs_you", []), ("choices", []), ("questions", []), ("advanced", []), ("steps", [])):
        report.setdefault(key, value)
    active_hints = hints if hints is not None else (for_url(page.url) or {})
    seen: set[str] = set()
    seen_fields = 0
    ready = False

    def show(status: str, detail: str = "", idx: int | None = None) -> None:
        payload: dict[str, Any] = {"status": status, "detail": detail}
        if idx is not None:
            payload["idx"] = idx
        page.evaluate(fillmod.LIVE_STATUS_JS, payload)

    def snap(name: str) -> None:
        if screenshot is None:
            return
        path = screenshot.with_name(f"{screenshot.stem}-{name}{screenshot.suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        report["steps"].append(str(path))

    def pause(reason: str, signature: str) -> bool:
        report["stop_reason"] = reason
        status, detail = _BANNERS[reason]
        show(status, detail)
        snap(reason)
        if resume_seconds <= 0:
            return False
        if on_stop:
            on_stop(page, reason)
        deadline = time.time() + resume_seconds
        while time.time() < deadline:
            try:
                if page.is_closed():
                    return False
                probe = page.evaluate(_PROBE_JS)
                fields = collect(page)
                buttons = page.evaluate(_BUTTONS_JS)
                if _signature(page, fields, buttons, probe) != signature:
                    report["resumed"] = True
                    report.pop("stop_reason", None)
                    return True
            except Exception:  # noqa: BLE001 - the page is navigating; look again
                return False
            page.wait_for_timeout(200)
        return False

    for step in range(max_steps):
        show("Reading application fields…" if step == 0 else f"Reading step {step + 1}…")
        fields = fillmod.wait_for_fields(page, field_wait_seconds if step == 0 else min(field_wait_seconds, 5))
        probe = page.evaluate(_PROBE_JS)
        buttons = page.evaluate(_BUTTONS_JS)
        signature = _signature(page, fields, buttons, probe)
        if signature in seen:
            if not pause("unknown", signature):
                break
            continue
        seen.add(signature)
        seen_fields += len(fields)
        if step == 0:
            log(f"Found {len(fields)} form fields.")
        text = _blob(probe)
        reason = _early_reason(probe, fields, buttons, active_hints)
        if reason == "no_form":
            report["note"] = "no_form_fields"
            report["stop_reason"] = "no_form"
            log("No form fields found. " + _NO_FORM)
            show("No form fields found", _NO_FORM)
            break
        if reason in _PAUSE or reason == "review":
            if not pause(reason, signature):
                break
            continue
        if not fields:
            button = pick_advance(buttons, final=False, hints=active_hints)
            if button is None:
                if not pause("unknown", signature):
                    break
                continue
            if not _click(page, button, report, log) or not _advanced(page, signature):
                if not pause("validation" if page.evaluate(_PROBE_JS).get("alert") else "unknown", signature):
                    break
            continue

        answers = _draft(page, fields, profile, answers, prepare_answers, show, fillmod)
        fields = collect(page)
        plan = map_fields(fields, profile, answers, memory_path=memory_path, model=model, use_model=use_model)
        if plan.get("model_error"):
            report["model_error"] = plan["model_error"]
            log(plan["model_error"])
        _apply_plan(page, plan, files, report, log, show, fillmod, headless=headless, live_delay_ms=live_delay_ms)
        snap(f"step-{step + 1}")
        probe = page.evaluate(_PROBE_JS)
        buttons = page.evaluate(_BUTTONS_JS)
        text = _blob(probe)
        if probe.get("captcha"):
            if not pause("captcha", signature):
                break
            continue
        if is_final_step(text):
            report["stop_reason"] = "review"
            show(*_BANNERS["review"])
            break
        button = pick_advance(buttons, final=False, hints=active_hints)
        if button is None:
            ready = True
            show("Ready for your review", f"{len(report['filled'])} fields filled · {len(report['uploaded'])} files uploaded. Check highlighted fields and submit yourself.")
            break
        if not _click(page, button, report, log) or not _advanced(page, signature):
            later = page.evaluate(_PROBE_JS)
            if not pause("validation" if later.get("alert") else "unknown", signature):
                break
            continue
    else:
        report["stop_reason"] = "unknown"
        show(*_BANNERS["unknown"])

    report["coverage"] = {
        "filled": len(report["filled"]),
        "uploaded": len(report["uploaded"]),
        "needs_you": len(report["needs_you"]),
        "seen": seen_fields,
        "host": urlsplit(getattr(page, "url", "") or "").netloc,
    }
    if ready and not report.get("note"):
        log(
            f"Done filling: {len(report['filled'])} filled, {len(report['uploaded'])} files, "
            f"{len(report['needs_you'])} for you, {len(report['questions'])} free-text questions, "
            f"{len(report['choices'])} choices. NOT submitted: review the form and press Submit yourself."
        )
    return report


def _draft(page, fields, profile, answers, prepare_answers, show, fillmod):
    if not prepare_answers:
        return answers
    planned = map_fields(fields, profile, answers, remember=False)
    questions = [item["label"] for item in planned["questions"] if item.get("label") and item["label"] not in answers]
    if not questions:
        return answers
    show("Drafting answers…", "The form will stay open while answers are prepared.")
    page.wait_for_timeout(100)
    try:
        drafted = prepare_answers(questions) or {}
    except Exception as err:  # noqa: BLE001 - drafting is optional; the form is still filled
        drafted = {}
        show("Drafting answers…", f"Continuing without drafted answers ({type(err).__name__}).")
    return {**answers, **drafted}


def _apply_plan(page, plan, files, report, log, show, fillmod, *, headless: bool, live_delay_ms: int) -> None:
    total = len(plan["fill"]) + len(plan["upload"])
    step = 0

    def show_action(item: dict[str, Any], verb: str) -> None:
        nonlocal step
        step += 1
        show(f"{verb}: {item.get('label') or item.get('which', 'field')}", f"Step {step} of {total} · You will review before submitting.", item.get("idx"))
        if not headless and live_delay_ms > 0:
            page.wait_for_timeout(live_delay_ms)

    for item in plan["fill"]:
        show_action(item, "Filling")
        if set_value(page, item, item["value_to_fill"]) is None:
            if not headless and live_delay_ms > 0:
                page.wait_for_timeout(live_delay_ms)
            report["filled"].append({"label": item["label"], "source": item["source"]})
            log(f"  filled: {item['label']} ({item['source']})")
        else:
            report["needs_you"].append({"label": item["label"], "why": "could not set"})
            log(f"  could not set: {item['label']}")

    for item in plan["upload"]:
        path = files.get(item["which"])
        if not path or not Path(path).is_file():
            report["needs_you"].append({"label": item["label"] or item["which"], "why": f"no {item['which']} PDF on disk"})
            continue
        show_action(item, "Uploading")
        if set_file(page, item, path) is None:
            if not headless and live_delay_ms > 0:
                page.wait_for_timeout(live_delay_ms)
            report["uploaded"].append({"label": item["label"], "file": Path(path).name})
            log(f"  uploaded {Path(path).name} to: {item['label'] or item['which']}")
        else:
            report["needs_you"].append({"label": item["label"], "why": "could not set"})

    seen_needs: set[str] = set()
    for item in plan["never"]:
        page.evaluate(fillmod.HIGHLIGHT_JS, {"idx": item["idx"], "text": "Answer this yourself"})
        shown = (item.get("ctx") or item["label"]) if item.get("type") in {"radio", "checkbox"} or item.get("kind") in {"radio", "checkbox", "buttons"} else item["label"]
        if shown not in seen_needs:
            seen_needs.add(shown)
            report["needs_you"].append({"label": shown, "why": _NEVER_WHY})
    for item in plan["questions"]:
        page.evaluate(fillmod.HIGHLIGHT_JS, {"idx": item["idx"], "text": "Free-text question (no drafted answer)"})
        report["questions"].append({"label": item["label"], "required": item["required"]})
    for item in plan["choice"]:
        if item.get("required"):
            page.evaluate(fillmod.HIGHLIGHT_JS, {"idx": item["idx"], "text": "Required: please choose"})
        report["choices"].append({"label": item["label"], "type": item.get("type"), "required": item.get("required")})


def _click(page: Any, button: dict[str, Any], report: dict[str, Any], log: Callable[[str], None]) -> bool:
    try:
        page.locator(f'[data-jd-btn="{button["idx"]}"]').click(timeout=5000)
    except Exception:  # noqa: BLE001 - a button that will not click is a stop, not a crash
        return False
    report["advanced"].append(button.get("text") or "")
    log(f"  continued: {button.get('text') or 'next'}")
    return True


def _advanced(page: Any, signature: str) -> bool:
    """True when the page structure changed after a click."""
    deadline = time.time() + 4
    while time.time() < deadline:
        try:
            probe = page.evaluate(_PROBE_JS)
            fields = collect(page)
            buttons = page.evaluate(_BUTTONS_JS)
            if _signature(page, fields, buttons, probe) != signature:
                return True
        except Exception:  # noqa: BLE001
            return True
        page.wait_for_timeout(150)
    return False

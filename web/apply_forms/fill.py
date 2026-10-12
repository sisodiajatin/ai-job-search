"""Drive the user's own Chrome to fill an application form, then stop. It never clicks Submit.

Design rules (see web/README.md):
- Deterministic code reads the page. No language model ever sees page content as instructions;
  free-text question labels are passed to a separate drafting call strictly as quoted data.
- Only profile facts, the two drafted PDFs and previously drafted answers are entered.
- Work authorization, sponsorship, demographics, salary, start date and consent are never filled:
  they are highlighted on the page and listed in the report for the user.
- After filling, the browser stays open for the user to review and press Submit. If a recognised
  confirmation page appears, the caller is told so it can mark the job applied.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .classify import classify
from .engine.fields import COLLECT_JS

PROFILE_DIR = Path(__file__).resolve().parents[1] / ".browser-profile"

HIGHLIGHT_JS = r"""
(args) => {
  const el = document.querySelector('[data-jd-idx="' + args.idx + '"]');
  if (!el) return;
  const box = el.closest('fieldset, [class*="field"], [class*="question"], [class*="Field"], [class*="Question"]') || el;
  box.style.outline = '3px solid #e8590c';
  box.style.outlineOffset = '4px';
  if (!box.querySelector('.jd-flag')) {
    const tag = document.createElement('div');
    tag.className = 'jd-flag';
    tag.textContent = args.text;
    tag.style.cssText = 'color:#e8590c;font:600 12px sans-serif;margin:4px 0;';
    box.prepend(tag);
  }
}
"""

LIVE_STATUS_JS = r"""
(args) => {
  let host = document.getElementById('jd-live-status');
  if (!host) {
    host = document.createElement('div');
    host.id = 'jd-live-status';
    host.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;pointer-events:none;';
    const shadow = host.attachShadow({mode:'open'});
    const style = document.createElement('style');
    style.textContent = `
      .panel {box-sizing:border-box;width:min(300px,calc(100vw - 32px));padding:16px 18px;
        background:#13251e;color:#fff;border:1px solid #517d64;border-radius:12px;
        box-shadow:0 6px 24px #0003;font:14px/1.5 system-ui,sans-serif;}
      .title {font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#a9dabd;}
      .status {margin-top:5px;font-weight:600;overflow-wrap:anywhere;}
      .detail {margin-top:4px;color:#cad7cf;font-size:12px;}
    `;
    shadow.append(style);
    const panel = document.createElement('div');
    panel.className = 'panel';
    panel.setAttribute('role','status');
    panel.setAttribute('aria-live','polite');
    for (const name of ['title','status','detail']) {
      const el = document.createElement('div');
      el.className = name;
      panel.append(el);
    }
    shadow.append(panel);
    document.documentElement.append(host);
    const activeStyle = document.createElement('style');
    activeStyle.textContent = '[data-jd-live-active] {outline:3px solid #2b9460 !important;outline-offset:4px !important;}';
    document.documentElement.append(activeStyle);
  }
  host.shadowRoot.querySelector('.title').textContent = 'Live application filling';
  host.shadowRoot.querySelector('.status').textContent = args.status;
  host.shadowRoot.querySelector('.detail').textContent = args.detail || 'You will review and submit yourself.';
  for (const el of document.querySelectorAll('[data-jd-live-active]')) el.removeAttribute('data-jd-live-active');
  if (args.idx !== undefined) {
    const el = document.querySelector('[data-jd-idx="' + args.idx + '"]');
    if (el) {
      const box = el.type === 'file' ? (el.closest('fieldset, [class*="field"], [class*="Field"]') || el.parentElement) : el;
      box.setAttribute('data-jd-live-active','');
      box.scrollIntoView({block:'center',behavior:'instant'});
    }
  }
}
"""

CONFIRM_PATTERNS = [
    r"thank you for (applying|your application|submitting)",
    r"your application (has been|was) (submitted|received|sent)",
    r"application (submitted|received)",
    r"we(?:'|’)?ve received your application",
    r"thanks for applying",
]
_confirm = [re.compile(p, re.I) for p in CONFIRM_PATTERNS]


def looks_confirmed(text: str) -> bool:
    return any(p.search(text or "") for p in _confirm)


def plan_actions(fields: list[dict[str, Any]], profile: dict[str, str], answers: dict[str, str]) -> dict[str, Any]:
    """Pure planning step: map every collected field to an action. Easy to unit-test."""
    plan: dict[str, Any] = {"fill": [], "upload": [], "never": [], "choice": [], "questions": [], "skipped_filled": []}
    for field in fields:
        category = classify(field)
        if category.startswith("profile:"):
            key = category.split(":", 1)[1]
            value = profile.get(key, "")
            if not value:
                plan["choice"].append(field)
            elif field.get("value"):
                plan["skipped_filled"].append(field)
            else:
                plan["fill"].append({**field, "value_to_fill": value, "source": key})
        elif category in {"file:cv", "file:cover"}:
            plan["upload"].append({**field, "which": category.split(":", 1)[1]})
        elif category == "never":
            plan["never"].append(field)
        elif category == "free_text":
            answer = answers.get(field["label"]) or answers.get(field["label"].rstrip("* ").strip())
            if answer and answer != "SKIP" and not field.get("value"):
                plan["fill"].append({**field, "value_to_fill": answer, "source": "drafted answer"})
            else:
                plan["questions"].append(field)
        else:
            plan["choice"].append(field)
    return plan


def wait_for_fields(page: Any, max_seconds: float = 15.0) -> list[dict[str, Any]]:
    """Single-page apps (Paycom, Workday and similar) render after domcontentloaded, so wait for the network to
    settle and for fields to appear before reading the page. Returns [] if nothing form-like ever shows up."""
    try:
        page.wait_for_load_state("networkidle", timeout=int(min(max_seconds, 8.0) * 1000))
    except Exception:  # noqa: BLE001 - pages with endless polling never go idle; keep going
        pass
    deadline = time.time() + max_seconds
    fields = page.evaluate(COLLECT_JS)
    while not fields and time.time() < deadline:
        page.wait_for_timeout(1000)
        fields = page.evaluate(COLLECT_JS)
    return fields


def collect_questions(url: str, profile: dict[str, str], answers: dict[str, str], *, headless: bool = True) -> list[str]:
    """Open the form read-only (nothing typed, nothing uploaded) and list the free-text questions."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), channel="chrome", headless=headless, viewport={"width": 1280, "height": 1600}
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            plan = plan_actions(wait_for_fields(page), profile, answers)
            return [q["label"] for q in plan["questions"] if q["label"]]
        finally:
            context.close()


def run_fill(
    url: str,
    profile: dict[str, str],
    files: dict[str, str | None],
    answers: dict[str, str],
    log: Callable[[str], None],
    *,
    headless: bool = False,
    screenshot: Path | None = None,
    wait_for_user_minutes: float = 60,
    on_confirmed: Callable[[], None] | None = None,
    after_fill: Callable[[Any], None] | None = None,
    prepare_answers: Callable[[list[str]], dict[str, str]] | None = None,
    live_delay_ms: int = 650,
    field_wait_seconds: float = 15.0,
    max_steps: int = 8,
    use_model: bool = False,
    memory_path: Path | None = None,
) -> dict[str, Any]:
    """Open ``url`` and fill one application. A single-page form is a one-step run."""
    from playwright.sync_api import sync_playwright

    from .engine.stepper import run_on_page

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"url": url, "filled": [], "uploaded": [], "needs_you": [], "choices": [], "questions": [], "confirmed": False}

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            slow_mo=0 if headless else 100,
            viewport=None if not headless else {"width": 1280, "height": 1600},
            args=["--start-maximized"] if not headless else [],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            log(f"Opening {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if not headless:
                page.bring_to_front()
            run_on_page(
                page, report, profile=profile, files=files, answers=answers, log=log,
                prepare_answers=prepare_answers, live_delay_ms=live_delay_ms,
                field_wait_seconds=field_wait_seconds, headless=headless, max_steps=max_steps,
                resume_seconds=0 if headless else wait_for_user_minutes * 60,
                use_model=use_model, memory_path=memory_path, screenshot=screenshot,
            )
            if report.get("note") == "no_form_fields":
                wait_for_user_minutes = min(wait_for_user_minutes, 2)
            if not headless:
                page.bring_to_front()
            if screenshot:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True)
                report["screenshot"] = str(screenshot)
            if after_fill:
                after_fill(page)

            # Stay open for the user. Watch only for a confirmation page. Account, code and
            # CAPTCHA stops already waited inside the stepper.
            if report.get("stop_reason") in {"login", "otp", "captcha", "user_only", "validation", "unknown"}:
                return report
            deadline = time.time() + wait_for_user_minutes * 60
            while time.time() < deadline and not headless:
                try:
                    if page.is_closed():
                        break
                    body = page.inner_text("body", timeout=3000)
                except Exception:  # noqa: BLE001 - page navigating or closed
                    time.sleep(2)
                    continue
                if looks_confirmed(body):
                    report["confirmed"] = True
                    log("Confirmation page detected: the application was submitted.")
                    if on_confirmed:
                        on_confirmed()
                    page.wait_for_timeout(4000)
                    break
                time.sleep(2)
        finally:
            context.close()
    return report

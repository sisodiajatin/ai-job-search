"""From a listing to the application form.

Board pages (Ashby, Greenhouse, Lever) stay the fast path. Anything else is opened and searched
for an Apply control by its text, role and aria label. New tabs and popups are followed. If none
of that works, the caller is told to paste the apply link.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..resolve import resolve
from .hints import for_url

_APPLY = re.compile(r"^(apply|apply now|apply for this (job|role|position))$", re.I)
_SKIP = re.compile(r"linkedin|indeed|glassdoor|facebook", re.I)
_NEEDS_LINK = "I could not find an Apply button on that page. Paste the application link and run again with --url."

_FIND_JS = r"""
() => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const visible = (el) => {
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || (el.offsetParent === null && s.position !== 'fixed'));
  };
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll('a, button, [role=button], [role=link]')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('value') || '');
    if (!text && !el.getAttribute('data-automation-id')) continue;
    el.setAttribute('data-jd-apply', String(i));
    out.push({
      idx: i, text, href: el.getAttribute('href') || '', tag: el.tagName.toLowerCase(),
      automation_id: el.getAttribute('data-automation-id') || '',
    });
    i += 1;
  }
  return out;
}
"""


def _http_url(url: str) -> str | None:
    parts = urlsplit(url or "")
    if parts.scheme in {"http", "https"} and parts.netloc:
        return url
    return None


def _candidate(control: dict[str, Any], hints: dict | None) -> bool:
    text = control.get("text") or ""
    if _SKIP.search(text):
        return False
    automation = control.get("automation_id") or ""
    if hints and automation in set(hints.get("discover_automation_ids") or []):
        return True
    if hints and automation in set(hints.get("never_automation_ids") or []):
        return False
    return bool(_APPLY.match(re.sub(r"\s+", " ", text).strip()))


def find_apply_link(url: str, *, company: str = "", title: str = "", headless: bool = True, profile_dir: Any = None) -> dict[str, str]:
    """Return ``{status: found|needs_link, apply_url?, via?, message?}``. Never raises on a missing control."""
    if company and title:
        found = resolve(company, title)
        if found.get("status") == "found" and found.get("apply_url"):
            return {"status": "found", "apply_url": found["apply_url"], "via": found.get("platform") or "board", "message": ""}
    opened = _open_and_find(url, headless=headless, profile_dir=profile_dir)
    return opened


def _open_and_find(url: str, *, headless: bool, profile_dir: Any) -> dict[str, str]:
    target = _http_url(url)
    if target is None:
        return {"status": "needs_link", "apply_url": "", "via": "", "message": _NEEDS_LINK}
    from playwright.sync_api import sync_playwright

    from ..fill import PROFILE_DIR

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir or PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 1600} if headless else None,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            return _from_page(page)
        except Exception:  # noqa: BLE001 - a page that will not load is a paste-the-link case
            return {"status": "needs_link", "apply_url": "", "via": "", "message": _NEEDS_LINK}
        finally:
            context.close()


def _from_page(page: Any) -> dict[str, str]:
    hints = for_url(page.url) or {}
    controls = page.evaluate(_FIND_JS) or []
    ranked = [control for control in controls if _candidate(control, hints)]
    ranked.sort(key=lambda control: 0 if _APPLY.match(re.sub(r"\s+", " ", control.get("text") or "").strip()) else 1)
    for control in ranked:
        href = control.get("href") or ""
        if href and not href.startswith(("#", "javascript:")):
            absolute = _http_url(urljoin(page.url, href))
            if absolute and absolute != page.url:
                return {"status": "found", "apply_url": absolute, "via": "listing", "message": ""}
        clicked = _click_control(page, control)
        if clicked:
            return clicked
    return {"status": "needs_link", "apply_url": "", "via": "", "message": _NEEDS_LINK}


def _click_control(page: Any, control: dict[str, Any]) -> dict[str, str] | None:
    before = page.url
    locator = page.locator(f'[data-jd-apply="{control["idx"]}"]')
    popup = None
    try:
        with page.context.expect_page(timeout=2500) as caught:
            locator.click(timeout=3000)
        popup = caught.value
    except Exception:  # noqa: BLE001 - most Apply controls navigate the same tab, or do nothing
        try:
            if page.locator(f'[data-jd-apply="{control["idx"]}"]').count():
                locator.click(timeout=3000)
        except Exception:  # noqa: BLE001
            return None
    if popup is not None:
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        found = _http_url(popup.url)
        if found:
            return {"status": "found", "apply_url": found, "via": "popup", "message": ""}
    try:
        page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001
        return None
    if page.url != before:
        found = _http_url(page.url)
        if found:
            return {"status": "found", "apply_url": found, "via": "listing", "message": ""}
    return None

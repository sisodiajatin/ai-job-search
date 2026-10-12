"""Set one field and read it back. A miss returns ``could not set`` and never raises."""

from __future__ import annotations

import re
from typing import Any

COULD_NOT_SET = "could not set"

_READ_JS = r"""
(idx) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('[data-jd-idx="' + idx + '"]')];
  if (!nodes.length) return null;
  const kind = nodes[0].getAttribute('data-jd-kind') || '';
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return clean(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return clean(wrap.innerText);
    return clean(el.getAttribute('aria-label') || el.innerText || el.textContent || el.value || '');
  };
  if (kind === 'checkbox') {
    const el = nodes[0];
    if (el.getAttribute('role') === 'checkbox') return el.getAttribute('aria-checked') === 'true' ? 'yes' : '';
    return el.checked ? 'yes' : '';
  }
  if (kind === 'radio' || kind === 'buttons') {
    const picked = nodes.length === 1
      ? [...nodes[0].querySelectorAll('input[type=radio], button, [role=radio], [role=button]')]
      : nodes;
    const on = picked.find((el) => el.checked || el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-checked') === 'true' || /\b(selected|active|is-selected)\b/.test(el.className || ''));
    return on ? labelOf(on) : '';
  }
  if (kind === 'select') {
    const el = nodes[0];
    return el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
  }
  if (kind === 'multiselect') {
    const root = nodes[0];
    return [...root.querySelectorAll('[role=option][aria-selected=true], option:checked')].map((el) => clean(el.innerText || el.textContent || el.value)).filter(Boolean).join(', ');
  }
  if (kind === 'date' && nodes[0].tagName !== 'INPUT') {
    const inputs = [...nodes[0].querySelectorAll('input')];
    const part = (re) => {
      const el = inputs.find((n) => re.test(((n.getAttribute('aria-label') || '') + ' ' + (n.getAttribute('placeholder') || '') + ' ' + (n.getAttribute('name') || '')).toLowerCase()));
      return el ? el.value : '';
    };
    return [part(/\bmonth\b/), part(/\bday\b/), part(/\byear\b/)].join('/');
  }
  if (kind === 'combobox') {
    const el = nodes[0];
    return clean(el.value || el.innerText || el.textContent || '');
  }
  const el = nodes[0];
  if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return el.value || '';
  const inner = el.querySelector('input, textarea');
  return inner ? inner.value || '' : '';
}
"""

_MARK_JS = r"""
(args) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const wanted = clean(args.value);
  const wantedFold = wanted.toLowerCase();
  for (const old of document.querySelectorAll('[data-jd-pick]')) old.removeAttribute('data-jd-pick');
  const nodes = [...document.querySelectorAll('[data-jd-idx="' + args.idx + '"]')];
  if (!nodes.length) return false;
  const kind = nodes[0].getAttribute('data-jd-kind') || '';
  const pool = nodes.length === 1 && (kind === 'buttons' || kind === 'radio' || kind === 'multiselect' || kind === 'combobox')
    ? [...nodes[0].querySelectorAll('input[type=radio], input[type=checkbox], button, [role=radio], [role=button], [role=option], li')]
    : nodes;
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return clean(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap && wrap !== el) return clean(wrap.innerText);
    return clean(el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('value') || '');
  };
  const hits = pool.filter((el) => {
    const text = labelOf(el);
    return text === wanted || text.toLowerCase() === wantedFold;
  });
  const exact = hits.find((el) => labelOf(el) === wanted) || (hits.length === 1 ? hits[0] : null);
  if (!exact) return false;
  exact.setAttribute('data-jd-pick', '1');
  return true;
}
"""


def _exact(options: list[str], value: str) -> str | None:
    wanted = re.sub(r"\s+", " ", value or "").strip()
    if not wanted:
        return None
    for option in options:
        if re.sub(r"\s+", " ", option).strip() == wanted:
            return option
    folded = [option for option in options if re.sub(r"\s+", " ", option).strip().lower() == wanted.lower()]
    if len(folded) == 1:
        return folded[0]
    return None


def read_value(page: Any, field: dict[str, Any]) -> str:
    try:
        got = page.evaluate(_READ_JS, str(field["idx"]))
    except Exception:  # noqa: BLE001 - a detached page is a failed read, not a crash
        return ""
    return "" if got is None else str(got)


def _matches(expected: str, actual: str) -> bool:
    left = re.sub(r"\s+", " ", expected or "").strip()
    right = re.sub(r"\s+", " ", actual or "").strip()
    return bool(left) and (left == right or left.lower() == right.lower())


def _split_date(value: str) -> tuple[str, str, str] | None:
    text = value.strip()
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return iso.group(2), iso.group(3), iso.group(1)
    us = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if us:
        return us.group(1).zfill(2), us.group(2).zfill(2), us.group(3)
    return None


def _click_marked(page: Any) -> bool:
    target = page.locator("[data-jd-pick='1']")
    if target.count() != 1:
        return False
    target.click(timeout=2000)
    return True


def set_value(page: Any, field: dict[str, Any], value: str) -> str | None:
    """Put ``value`` into ``field``. Returns None on success, or ``could not set``."""
    try:
        return _set_value(page, field, value)
    except Exception:  # noqa: BLE001 - one stubborn widget must not abort the form
        return COULD_NOT_SET


def set_file(page: Any, field: dict[str, Any], path: str) -> str | None:
    try:
        page.locator(f'[data-jd-idx="{field["idx"]}"]').first.set_input_files(path, timeout=8000)
        name = page.locator(f'[data-jd-idx="{field["idx"]}"]').first.evaluate("el => (el.files && el.files[0] && el.files[0].name) || ''")
        if not name:
            return COULD_NOT_SET
        return None
    except Exception:  # noqa: BLE001
        return COULD_NOT_SET


def _set_value(page: Any, field: dict[str, Any], value: str) -> str | None:
    kind = (field.get("kind") or "text").lower()
    idx = str(field["idx"])
    root = page.locator(f'[data-jd-idx="{idx}"]').first

    if kind in {"text", "textarea"}:
        root.fill(value, timeout=5000)
        if _matches(value, read_value(page, field)):
            return None
        return COULD_NOT_SET

    if kind == "date" and field.get("tag") == "input":
        root.fill(value, timeout=5000)
        if _matches(value, read_value(page, field)):
            return None
        return COULD_NOT_SET

    if kind == "date":
        parts = _split_date(value)
        if not parts:
            return COULD_NOT_SET
        month, day, year = parts
        wrote = root.evaluate(
            """(el, parts) => {
              const inputs = [...el.querySelectorAll('input')];
              const set = (re, val) => {
                const node = inputs.find((n) => re.test(((n.getAttribute('aria-label') || '') + ' ' + (n.getAttribute('placeholder') || '') + ' ' + (n.getAttribute('name') || '')).toLowerCase()));
                if (!node) return false;
                node.value = val;
                node.dispatchEvent(new Event('input', {bubbles: true}));
                node.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
              };
              return set(/\\bmonth\\b/, parts.month) && set(/\\bday\\b/, parts.day) && set(/\\byear\\b/, parts.year);
            }""",
            {"month": month, "day": day, "year": year},
        )
        actual = read_value(page, field)
        if wrote and actual.split("/") == [month, day, year]:
            return None
        return COULD_NOT_SET

    if kind == "checkbox":
        want = value.strip().lower() in {"yes", "true", "1", "on", "checked"}
        role = root.get_attribute("role")
        if role == "checkbox":
            current = root.get_attribute("aria-checked") == "true"
            if current != want:
                try:
                    root.click(timeout=2000)
                except Exception:  # noqa: BLE001 - a custom checkbox with no box still has a click handler
                    root.evaluate("el => el.click()")
        elif want:
            root.check(timeout=2000)
        else:
            root.uncheck(timeout=2000)
        got = read_value(page, field) == "yes"
        if got == want:
            return None
        return COULD_NOT_SET

    if kind == "select":
        match = _exact(list(field.get("options") or []), value)
        if match is None:
            return COULD_NOT_SET
        root.select_option(label=match, timeout=3000)
        if _matches(match, read_value(page, field)):
            return None
        return COULD_NOT_SET

    if kind in {"radio", "buttons", "combobox", "multiselect"}:
        targets = [part.strip() for part in value.split(",")] if kind == "multiselect" else [value]
        if kind in {"combobox", "multiselect"}:
            root.click(timeout=2000)
            page.wait_for_timeout(100)
        for target in targets:
            match = target if kind == "combobox" else (_exact(list(field.get("options") or []), target) or target)
            if kind != "combobox" and _exact(list(field.get("options") or []), target) is None:
                return COULD_NOT_SET
            marked = page.evaluate(_MARK_JS, {"idx": idx, "value": match})
            if not marked or not _click_marked(page):
                if kind == "combobox":
                    option = page.get_by_role("option", name=match, exact=True)
                    if option.count() != 1:
                        return COULD_NOT_SET
                    option.click(timeout=2000)
                else:
                    return COULD_NOT_SET
        actual = read_value(page, field)
        if kind == "multiselect":
            got = {part.strip().lower() for part in actual.split(",") if part.strip()}
            if {part.lower() for part in targets} <= got:
                return None
            return COULD_NOT_SET
        if _matches(value if kind != "buttons" and kind != "radio" else (_exact(list(field.get("options") or []), value) or value), actual):
            return None
        return COULD_NOT_SET

    return COULD_NOT_SET

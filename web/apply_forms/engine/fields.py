"""One field model for every application widget, collected by accessibility semantics.

`COLLECT_JS` is the page script `fill.wait_for_fields` runs. It marks each field with
`data-jd-idx` and returns plain dicts. It never clicks, never types, and never reads a
password (those inputs are skipped; the caller detects them separately).
"""

from __future__ import annotations

from typing import Any

COLLECT_JS = r"""
() => {
  const clean = (t, n = 300) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || (el.offsetParent === null && s.position !== 'fixed'));
  };
  for (const old of document.querySelectorAll('[data-jd-idx]')) old.removeAttribute('data-jd-idx');

  const textOf = (el) => clean(el ? (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('value') || '') : '');
  const labelOf = (el) => {
    if (!el) return '';
    let t = '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) t = l.innerText;
    }
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(/\s+/).map((id) => textOf(document.getElementById(id))).join(' ');
    }
    if (!t) {
      const l = el.closest('label');
      if (l) t = l.innerText;
    }
    return clean(t);
  };
  const legendOf = (el) => {
    const fs = el.closest('fieldset, [role=radiogroup], [role=group]');
    if (!fs) return '';
    const labelled = fs.getAttribute('aria-labelledby');
    const lg = fs.querySelector('legend') || (labelled && document.getElementById(labelled.split(/\s+/)[0]));
    return clean(lg ? (lg.innerText || lg.textContent) : fs.getAttribute('aria-label') || '');
  };
  const contextOf = (el) => {
    const holder = el.closest('fieldset, [class*="field"], [class*="question"], [class*="Field"], [class*="Question"]') || el.parentElement;
    if (!holder) return '';
    const h = holder.querySelector('legend, label, h1, h2, h3, h4, [class*="label"]');
    return h ? clean(h.innerText) : '';
  };
  const optionText = (el) => {
    if (!el) return '';
    const own = labelOf(el);
    if (own && own.length < 80) return own;
    return textOf(el).slice(0, 80);
  };

  const claimed = new Set();
  const out = [];
  let i = 0;
  const push = (nodes, data) => {
    const list = [].concat(nodes);
    for (const node of list) {
      if (!node) continue;
      node.setAttribute('data-jd-idx', String(i));
      node.setAttribute('data-jd-kind', data.kind);
      claimed.add(node);
    }
    out.push(Object.assign({ idx: i }, data));
    i += 1;
  };

  const dateName = (el) => (labelOf(el) + ' ' + (el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('name') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();

  for (const box of document.querySelectorAll('fieldset, [role=group]')) {
    const inputs = [...box.querySelectorAll('input')].filter((el) => visible(el) && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
    if (inputs.length < 2 || inputs.length > 4) continue;
    const names = inputs.map(dateName);
    if (names.some((n) => /\bmonth\b/.test(n)) && names.some((n) => /\bday\b/.test(n)) && names.some((n) => /\byear\b/.test(n))) {
      inputs.forEach((el) => claimed.add(el));
      const label = legendOf(box) || contextOf(box) || 'Date';
      push(box, {
        tag: box.tagName.toLowerCase(), type: 'date', kind: 'date', role: box.getAttribute('role') || '', popup: '',
        label, ctx: label, required: inputs.some((el) => el.required || el.getAttribute('aria-required') === 'true'),
        name: '', value: '', options: [],
      });
    }
  }

  for (const box of document.querySelectorAll('[aria-multiselectable=true], [role=listbox][aria-multiselectable=true]')) {
    if (!visible(box) || claimed.has(box)) continue;
    const options = [...box.querySelectorAll('[role=option]')].map((el) => textOf(el)).filter(Boolean).slice(0, 80);
    [...box.querySelectorAll('input, textarea')].forEach((el) => claimed.add(el));
    push(box, {
      tag: box.tagName.toLowerCase(), type: 'multiselect', kind: 'multiselect', role: box.getAttribute('role') || '',
      popup: box.getAttribute('aria-haspopup') || '', label: labelOf(box) || legendOf(box) || contextOf(box),
      ctx: legendOf(box) || contextOf(box), required: box.getAttribute('aria-required') === 'true',
      name: box.getAttribute('name') || '', value: '', options,
    });
  }

  const nav = /^(submit|next|continue|back|cancel|save|apply|sign|log|review|finish|send|place)\b/i;
  const yesNo = /^(yes|no|y|n|true|false)$/i;
  for (const box of document.querySelectorAll('[role=radiogroup], [role=group], fieldset, div, li, section')) {
    if (claimed.has(box) || /^(form|body|main|html)$/i.test(box.tagName)) continue;
    const radios = [...box.querySelectorAll(':scope > [role=radio]')];
    if (radios.length >= 2 && radios.every(visible)) {
      const options = radios.map(optionText).filter(Boolean);
      if (options.length >= 2) {
        push(radios, {
          tag: box.tagName.toLowerCase(), type: 'radio', kind: 'radio', role: 'radio', popup: '',
          label: labelOf(box) || legendOf(box) || contextOf(box), ctx: legendOf(box) || contextOf(box),
          required: box.getAttribute('aria-required') === 'true' || radios.some((el) => el.getAttribute('aria-required') === 'true'),
          name: '', value: optionText(radios.find((el) => el.getAttribute('aria-checked') === 'true')) || '', options,
        });
      }
      continue;
    }
    let buttons = [...box.querySelectorAll(':scope > button, :scope > [role=button]')].filter((el) => visible(el) && !claimed.has(el));
    if (buttons.length < 2) {
      const inner = box.querySelector(':scope > div, :scope > span');
      if (inner && box.querySelectorAll('button, [role=button]').length <= 6) {
        buttons = [...inner.querySelectorAll(':scope > button, :scope > [role=button]')].filter((el) => visible(el) && !claimed.has(el));
      }
    }
    if (buttons.length < 2 || buttons.length > 6) continue;
    if (box.querySelector('input, select, textarea')) continue;
    const options = buttons.map(optionText);
    if (options.some((t) => !t || nav.test(t))) continue;
    const role = box.getAttribute('role') || '';
    const paired = options.filter((t) => yesNo.test(t)).length >= 2;
    if (!paired && role !== 'group' && role !== 'radiogroup') continue;
    buttons.forEach((button) => claimed.add(button));
    const previous = box.previousElementSibling;
    const label = labelOf(box) || legendOf(box) || (previous ? textOf(previous) : '') || contextOf(box);
    push(box, {
      tag: box.tagName.toLowerCase(), type: 'buttons', kind: 'buttons', role, popup: '',
      label: clean(label), ctx: clean(label), required: box.getAttribute('aria-required') === 'true',
      name: '', value: optionText(buttons.find((el) => el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-checked') === 'true')) || '',
      options,
    });
  }

  const native = [...document.querySelectorAll('input, textarea, select')];
  const radioSeen = new Set();
  for (const el of native) {
    if (claimed.has(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (type === 'password') continue;
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    const isFile = type === 'file';
    if (!isFile && !visible(el)) continue;

    if (type === 'radio') {
      if (radioSeen.has(el)) continue;
      const name = el.getAttribute('name') || '';
      const group = name
        ? [...document.querySelectorAll('input[type=radio][name="' + CSS.escape(name) + '"]')].filter((node) => !claimed.has(node))
        : [el];
      group.forEach((node) => radioSeen.add(node));
      const options = group.map(optionText).filter(Boolean);
      const checked = group.find((node) => node.checked);
      const holder = el.closest('fieldset, [role=radiogroup]');
      const label = (holder && (legendOf(holder) || labelOf(holder))) || contextOf(el) || labelOf(el);
      push(group, {
        tag: 'input', type: 'radio', kind: 'radio', role: el.getAttribute('role') || '', popup: '',
        label: clean(label), ctx: legendOf(el) || contextOf(el) || clean(label),
        required: group.some((node) => node.required || node.getAttribute('aria-required') === 'true'),
        name, value: checked ? optionText(checked) : '', options,
      });
      continue;
    }

    const role = el.getAttribute('role') || '';
    const popup = el.getAttribute('aria-haspopup') || '';
    let kind = 'text';
    if (isFile) kind = 'file';
    else if (type === 'checkbox' || role === 'checkbox') kind = 'checkbox';
    else if (tag === 'select') kind = el.multiple ? 'multiselect' : 'select';
    else if (tag === 'textarea') kind = 'textarea';
    else if (type === 'date' || type === 'month' || type === 'week' || type === 'datetime-local') kind = 'date';
    else if (role === 'combobox' || ['listbox', 'true', 'menu'].includes(popup.toLowerCase())) kind = 'combobox';
    let options = tag === 'select' ? [...el.options].slice(0, 80).map((o) => clean(o.text, 80)) : [];
    if (kind === 'combobox') {
      const list = document.getElementById(el.getAttribute('aria-controls') || '');
      if (list) options = [...list.querySelectorAll('[role=option]')].map((o) => textOf(o)).filter(Boolean).slice(0, 80);
    }
    let value = '';
    if (type === 'checkbox') value = el.checked ? 'yes' : '';
    else if (tag === 'select') {
      const chosen = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
      const placeholder = chosen && (chosen.value === '' || chosen.disabled || /^(select|choose|please select|--|\u2014|pick)\b/i.test(clean(chosen.text)));
      value = chosen && !placeholder ? clean(chosen.text, 80) : '';
    }
    else if (!isFile) value = el.value || '';
    const label = labelOf(el) || contextOf(el) || el.getAttribute('placeholder') || el.getAttribute('name') || '';
    push(el, {
      tag, type, kind, role, popup, label: clean(label), ctx: contextOf(el),
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      name: el.getAttribute('name') || '', value, options,
    });
  }

  for (const el of document.querySelectorAll('[role=checkbox], [role=combobox], button[aria-haspopup=listbox]')) {
    if (claimed.has(el) || !visible(el)) continue;
    if (el.closest('[data-jd-idx]')) continue;
    const role = el.getAttribute('role') || '';
    const kind = role === 'checkbox' ? 'checkbox' : (el.getAttribute('aria-multiselectable') === 'true' ? 'multiselect' : 'combobox');
    const listId = el.getAttribute('aria-controls');
    const list = listId ? document.getElementById(listId) : el;
    const options = list ? [...list.querySelectorAll('[role=option]')].map(textOf).filter(Boolean).slice(0, 80) : [];
    push(el, {
      tag: el.tagName.toLowerCase(), type: kind, kind, role, popup: el.getAttribute('aria-haspopup') || '',
      label: labelOf(el) || contextOf(el), ctx: contextOf(el),
      required: el.getAttribute('aria-required') === 'true',
      name: el.getAttribute('name') || '', value: '', options,
    });
  }
  return out;
}
"""


def collect(page: Any) -> list[dict[str, Any]]:
    """Read the current page's fields. Safe to call more than once; indexes are reassigned."""
    fields = page.evaluate(COLLECT_JS)
    return list(fields or [])

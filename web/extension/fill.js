// Applies a server plan to the page. Touches only fields the server named (by data-jd-idx from
// collect.js), reads every value back, never clicks a submit-like control, never touches a password.
window.__jdApply = (plan) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const SUBMITISH = /^(submit|send|finish|confirm|place|complete|apply|next|continue|save)\b/i;
  const nodesOf = (idx) => [...document.querySelectorAll('[data-jd-idx="' + idx + '"]')];
  const outline = (nodes, color) => {
    for (const el of nodes) { el.style.outline = '3px solid ' + color; el.style.outlineOffset = '2px'; }
  };
  const fire = (el, names) => { for (const n of names) el.dispatchEvent(new Event(n, { bubbles: true })); };
  const setNative = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    fire(el, ['input', 'change']);
  };
  const same = (a, b) => clean(a).toLowerCase() === clean(b).toLowerCase() && clean(a) !== '';
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); }
    const wrap = el.closest('label');
    if (wrap && wrap !== el) return clean(wrap.innerText);
    return clean(el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('value') || '');
  };
  const isOn = (el) => el.checked === true || el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-checked') === 'true' || /\b(selected|active|is-selected)\b/.test(el.className || '');

  const setOne = (item) => {
    const nodes = nodesOf(item.idx);
    if (!nodes.length) return ['failed', 'field no longer on page'];
    const first = nodes[0];
    const type = (first.getAttribute('type') || '').toLowerCase();
    if (type === 'password' || type === 'file' || type === 'submit') return ['skipped', 'not a fillable field'];
    const kind = item.kind;
    const value = String(item.value || '');
    if (kind === 'text' || kind === 'textarea' || (kind === 'date' && first.tagName === 'INPUT')) {
      first.focus();
      setNative(first, value);
      return first.value === value ? ['filled', ''] : ['failed', 'page rejected the value'];
    }
    if (kind === 'select') {
      const options = [...first.options];
      const hits = options.filter((o) => same(o.text, value));
      if (hits.length !== 1) return ['failed', 'option not offered'];
      setNative(first, hits[0].value);
      return same(first.options[first.selectedIndex].text, value) ? ['filled', ''] : ['failed', 'page rejected the value'];
    }
    if (kind === 'checkbox') {
      const want = ['yes', 'true', '1', 'on', 'checked'].includes(value.toLowerCase());
      const role = first.getAttribute('role') === 'checkbox';
      const current = role ? first.getAttribute('aria-checked') === 'true' : first.checked;
      if (current !== want) first.click();
      const now = role ? first.getAttribute('aria-checked') === 'true' : first.checked;
      return now === want ? ['filled', ''] : ['failed', 'checkbox did not change'];
    }
    if (kind === 'radio' || kind === 'buttons') {
      const pool = nodes.length === 1
        ? [...first.querySelectorAll('input[type=radio], button, [role=radio], [role=button]')]
        : nodes;
      const hits = pool.filter((el) => same(labelOf(el), value));
      if (hits.length !== 1) return ['failed', 'option not found'];
      const target = hits[0];
      if (target.tagName === 'BUTTON' && ((target.getAttribute('type') || '').toLowerCase() === 'submit' || SUBMITISH.test(labelOf(target)) && !/^(yes|no)$/i.test(labelOf(target)))) {
        return ['skipped', 'looks like a navigation or submit button'];
      }
      target.click();
      return isOn(target) ? ['filled', ''] : ['failed', 'could not confirm the choice'];
    }
    return ['skipped', 'widget not supported yet (' + kind + ')'];
  };

  const results = [];
  for (const item of plan.fill || []) {
    let status = 'failed', why = '';
    try { [status, why] = setOne(item); } catch (e) { why = String(e && e.message || e); }
    results.push({ idx: item.idx, label: item.label, status, why });
    const nodes = nodesOf(item.idx);
    outline(nodes, status === 'filled' ? '#1a9f4b' : '#d98a00');
  }
  for (const up of plan.upload || []) {
    const file = (plan.files || {})[up.which];
    const el = nodesOf(up.idx)[0];
    let status = 'skipped', why = 'no draft selected';
    if (file && el && (el.getAttribute('type') || '').toLowerCase() === 'file') {
      try {
        const bin = atob(file.b64);
        const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
        const dt = new DataTransfer();
        dt.items.add(new File([bytes], file.name, { type: 'application/pdf' }));
        el.files = dt.files;
        fire(el, ['input', 'change']);
        [status, why] = el.files.length === 1 && el.files[0].name === file.name ? ['filled', ''] : ['failed', 'page rejected the file'];
      } catch (e) { status = 'failed'; why = String(e && e.message || e); }
    }
    results.push({ idx: up.idx, label: up.label + ' (' + up.which + ')', status, why });
    outline([el], status === 'filled' ? '#1a9f4b' : '#d98a00');
  }
  for (const f of plan.never || []) outline(nodesOf(f.idx), '#d98a00');
  for (const f of plan.left_for_you || []) outline(nodesOf(f.idx), '#d98a00');
  return results;
};

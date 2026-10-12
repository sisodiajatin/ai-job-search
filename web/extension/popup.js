const out = document.getElementById("out");
const text = (s) => { const d = document.createElement("div"); d.textContent = s; return d; };

function section(title, items, fmt) {
  if (!items.length) return;
  const h = document.createElement("h4");
  h.textContent = title + " (" + items.length + ")";
  const ul = document.createElement("ul");
  for (const it of items) { const li = document.createElement("li"); li.textContent = fmt(it); ul.append(li); }
  out.append(h, ul);
}

document.getElementById("save").onclick = async () => {
  const t = document.getElementById("token").value.trim();
  await chrome.storage.local.set({ token: t });
  document.getElementById("token").value = "";
  out.replaceChildren(text("Token saved."));
  preview().catch(fail);
};

async function preview(fill = false) {
  out.replaceChildren(text("Reading page..."));
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const r = await chrome.runtime.sendMessage({ type: "run", tabId: tab.id, fill });
  if (r.error) return fail(new Error(r.error));
  out.replaceChildren();
  if (fill) {
    section("Filled", r.results.filter((x) => x.status === "filled"), (x) => x.label);
    section("Could not set (yours)", r.results.filter((x) => x.status !== "filled"), (x) => x.label + " - " + x.why);
    out.append(text("Review the page. Orange = yours to answer. Nothing was submitted."));
  } else {
    section("Would fill", r.plan.fill, (f) => f.label + " <- " + f.source);
    section("Would upload", r.plan.upload, (f) => f.label + " (" + f.which + ")");
  }
  section("Never filled (yours)", r.plan.never, (f) => f.label);
  section("Left for you", r.plan.left_for_you, (f) => f.label);
  if (!r.fields) out.append(text("No form fields found on this page."));
}

function fail(e) {
  const d = text("Error: " + (e && e.message ? e.message : e));
  d.className = "err";
  out.replaceChildren(d);
}

document.getElementById("fill").onclick = () => preview(true).catch(fail);
document.getElementById("preview").onclick = () => preview().catch(fail);

(async () => {
  const sel = document.getElementById("draft");
  const { draftKey } = await chrome.storage.local.get("draftKey");
  const r = await chrome.runtime.sendMessage({ type: "drafts" });
  for (const d of r.drafts || []) sel.append(new Option(d.label, d.key, false, d.key === draftKey));
  sel.append(new Option("No CV/cover (skip uploads)", ""));
  if (draftKey === "") sel.value = "";
  else if (!draftKey && r.drafts && r.drafts[0]) chrome.storage.local.set({ draftKey: r.drafts[0].key });
  sel.onchange = () => chrome.storage.local.set({ draftKey: sel.value });
})();

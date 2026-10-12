// Talks to the local Job Desk server and runs the fill. Profile data never leaves this machine.
const SERVER = "http://127.0.0.1:8787";

async function api(path, options = {}) {
  const { token } = await chrome.storage.local.get("token");
  if (!token) throw new Error("Paste the token first.");
  let res;
  try {
    res = await fetch(SERVER + path, { ...options, headers: { ...options.headers, "X-Job-Desk-Token": token } });
  } catch (e) {
    throw new Error("Cannot reach " + SERVER + ". Start python web/run.py.");
  }
  if (!res.ok) throw new Error("Server said " + res.status + " for " + path.split("?")[0] + ".");
  return res;
}

async function fileB64(key, which) {
  const res = await api("/api/ext/file?key=" + encodeURIComponent(key) + "&which=" + which).catch(() => null);
  if (!res) return null;
  const bytes = new Uint8Array(await res.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// Read the page, ask the server for a plan, and (when fill is true) apply it. Never submits.
async function run(tabId, fill) {
  const inject = (files) => chrome.scripting.executeScript({ target: { tabId }, files });
  const call = async (func, args = []) => (await chrome.scripting.executeScript({ target: { tabId }, func, args }))[0].result;
  await inject(["collect.js"]);
  const fields = await call(() => window.__jdCollect());
  const plan = await (await api("/api/ext/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields }) })).json();
  if (!fill) return { plan, fields: fields.length };
  const { draftKey } = await chrome.storage.local.get("draftKey");
  plan.files = {};
  for (const which of new Set(plan.upload.map((u) => u.which))) {
    const b64 = draftKey && (await fileB64(draftKey, which));
    if (b64) plan.files[which] = { b64, name: which === "cover" ? "Jatin_Sisodia_Cover_Letter.pdf" : "Jatin_Sisodia_CV.pdf" };
  }
  await inject(["fill.js"]);
  const results = await call((p) => window.__jdApply(p), [plan]);
  return { plan, results, fields: fields.length };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const jobs = {
    run: () => run(msg.tabId, msg.fill),
    drafts: async () => ({ drafts: await (await api("/api/ext/drafts")).json() }),
  };
  if (!jobs[msg.type]) return false;
  jobs[msg.type]().then(reply, (e) => reply({ error: e.message }));
  return true;
});

// Alt+Shift+F: fill the current tab without opening the popup. The badge shows the result.
chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "fill-page") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const badge = (text, color) => { chrome.action.setBadgeBackgroundColor({ color }); chrome.action.setBadgeText({ text }); setTimeout(() => chrome.action.setBadgeText({ text: "" }), 8000); };
  try {
    const { results } = await run(tab.id, true);
    badge(String(results.filter((r) => r.status === "filled").length), "#1a9f4b");
  } catch (e) {
    badge("!", "#b00020");
  }
});

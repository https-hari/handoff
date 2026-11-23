const $ = (id) => document.getElementById(id);
const TARGET_URLS = {
  chatgpt: "https://chatgpt.com/",
  claude: "https://claude.ai/new",
  gemini: "https://gemini.google.com/app",
};
let tab = null;        // current tab
let pageSite = null;   // "chatgpt" | "claude" | "gemini" | null
let state = {};        // { capture, handoff, verify, backend }

init();

async function init() {
  state = await chrome.storage.local.get(["capture", "handoff", "verify", "backend"]);
  if (state.backend) $("backend").value = state.backend;

  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "ping" });
    if (r?.ok) { pageSite = r.site; $("site").textContent = `On ${r.label}`; }
  } catch { /* not a supported page */ }
  if (!pageSite) $("site").textContent = "Open ChatGPT, Claude or Gemini";

  // suggest a target different from the source
  const src = state.capture?.site;
  if (src && $("target").value === src) $("target").value = src === "claude" ? "chatgpt" : "claude";

  render();
  wire();
}

function wire() {
  $("captureBtn").onclick = capture;
  $("pasteBtn").onclick = usePaste;
  $("exportBtn").onclick = exportJson;
  $("buildBtn").onclick = build;
  $("insertBtn").onclick = insert;
  $("copyBtn").onclick = () => copy(state.handoff.prompt, "Handoff copied. Paste it into the new chat.");
  $("openBtn").onclick = () => chrome.tabs.create({ url: TARGET_URLS[state.handoff.target] || TARGET_URLS.claude });
  $("checkBtn").onclick = check;
  $("copyMissingBtn").onclick = () => copy(
    "Please also follow these rules for the rest of this chat:\n" +
      state.verify.missing.map((r, i) => `${i + 1}. ${r}`).join("\n"),
    "Missing rules copied. Send them in the chat.");
  $("backend").onchange = () => save({ backend: $("backend").value.replace(/\/$/, "") });
}

function render() {
  const c = state.capture;
  $("captureBtn").disabled = !pageSite;
  if (c) {
    const when = new Date(c.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("captureStatus").textContent =
      `${c.messages.length} messages from ${c.label}${c.auto ? " (saved at usage limit)" : ""} · ${when}`;
  }
  $("exportBtn").hidden = !c;
  $("buildBtn").disabled = !c;

  const h = state.handoff;
  $("result").hidden = !h;
  if (h) {
    const s = h.stats;
    const rules = h.card.instructions.length;
    const code = h.card.artifacts.length;
    $("stats").textContent =
      `${s.prompt_tokens.toLocaleString()} tokens instead of ${s.source_tokens.toLocaleString()} ` +
      `(${Math.round(s.compression * 100)}%) · ${rules} rule${rules === 1 ? "" : "s"} · ` +
      `${code} code block${code === 1 ? "" : "s"}`;
    $("goal").textContent = `Goal: ${h.card.goal}`;
    $("insertBtn").disabled = !pageSite;
    $("openBtn").textContent = `Open ${$("target").selectedOptions[0]?.text || ""}`.trim();
    $("openBtn").hidden = !TARGET_URLS[h.target];
  }
  $("checkBtn").disabled = !h || !pageSite;

  const v = state.verify;
  $("check").hidden = !v;
  if (v) {
    const kept = v.checks.length - v.missing.length;
    $("checkSummary").innerHTML = "";
    const span = document.createElement("span");
    span.className = v.missing.length ? "bad" : "ok";
    span.textContent = v.checks.length
      ? `${kept}/${v.checks.length} rules kept` : "No rules to check";
    $("checkSummary").append(span, ` · goal ${v.goal_correct ? "correct" : "wrong"}`);
    $("missingList").replaceChildren(...v.missing.map((r) => Object.assign(document.createElement("li"), { textContent: r })));
    $("copyMissingBtn").hidden = !v.missing.length;
  }
}

async function capture() {
  try {
    say("Capturing… the page will scroll through the chat. Keep this popup open.");
    $("captureBtn").disabled = true;
    const r = await chrome.tabs.sendMessage(tab.id, { type: "capture" });
    $("captureBtn").disabled = false;
    if (!r?.ok) return say(r?.error || "Capture failed.");
    if (!r.messages.length) return say("No messages found. The page layout may have changed; use the paste option.");
    const label = { chatgpt: "ChatGPT", claude: "Claude", gemini: "Gemini" }[r.site];
    await save({ capture: { site: r.site, label, messages: r.messages, at: Date.now() }, handoff: null, verify: null });
    say("Captured.");
  } catch {
    say("Couldn't reach the page. Reload it and try again.");
  }
}

async function usePaste() {
  const messages = parsePasted($("pasteBox").value);
  if (messages.length < 2) return say('Start each turn with "User:" or "Assistant:".');
  await save({ capture: { site: "paste", label: "pasted chat", messages, at: Date.now() }, handoff: null, verify: null });
  say(`Using ${messages.length} pasted messages.`);
}

function parsePasted(text) {
  const role = /^(user|you|me|assistant|chatgpt|claude|gemini|ai)\s*:\s*/i;
  const out = [];
  for (const line of text.split("\n")) {
    const m = line.match(role);
    if (m) {
      const r = /^(user|you|me)$/i.test(m[1]) ? "user" : "assistant";
      out.push({ role: r, content: line.slice(m[0].length) });
    } else if (out.length) {
      out[out.length - 1].content += "\n" + line;
    }
  }
  return out.map((m) => ({ ...m, content: m.content.trim() })).filter((m) => m.content);
}

async function build() {
  const target = $("target").value;
  say("Building handoff…");
  $("buildBtn").disabled = true;
  try {
    const body = { conversation: { source: state.capture.site, messages: state.capture.messages }, target };
    const res = await api("/handoff", body);
    await save({ handoff: { ...res, target }, verify: null });
    say("Ready. Open the new assistant, then Insert here (or Copy and paste).");
  } catch (e) {
    say(e.message);
  } finally {
    $("buildBtn").disabled = !state.capture;
  }
}

async function insert() {
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "insert", text: state.handoff.prompt });
    say(r?.ok ? "Inserted. Review it, then press send." : r?.error || "Insert failed.");
  } catch {
    say("Couldn't reach the page. Use Copy instead.");
  }
}

async function check() {
  say("Checking the reply…");
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "lastReply" });
    if (!r?.ok) return say(r?.error || "No reply found.");
    const res = await api("/verify", { handoff_id: state.handoff.handoff_id, card: state.handoff.card, reply: r.text });
    await save({ verify: res });
    say(res.missing.length ? "Some rules were dropped. Copy them back into the chat." : "All rules carried over.");
  } catch (e) {
    say(e.message);
  }
}

function exportJson() {
  const c = state.capture;
  const blob = new Blob([JSON.stringify({ source: c.site, messages: c.messages }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  chrome.tabs.create({ url });
}

async function api(path, body) {
  const base = ($("backend").value || "http://localhost:8000").replace(/\/$/, "");
  let res;
  try {
    res = await fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error(`Backend not reachable at ${base}. Is it running?`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Backend error (${res.status}).`);
  return data;
}

async function save(patch) {
  Object.assign(state, patch);
  await chrome.storage.local.set(patch);
  render();
}

async function copy(text, okMsg) {
  try { await navigator.clipboard.writeText(text); say(okMsg); }
  catch { say("Copy failed."); }
}

function say(text) { $("message").textContent = text; }

const $ = (id) => document.getElementById(id);
const TARGET_URLS = {
  chatgpt: "https://chatgpt.com/",
  claude: "https://claude.ai/new",
  gemini: "https://gemini.google.com/app",
};
const LABELS = { chatgpt: "ChatGPT", claude: "Claude", gemini: "Gemini", generic: "Other", paste: "Pasted chat" };
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;

let tab = null;        // current tab
let pageSite = null;   // "chatgpt" | "claude" | "gemini" | null
let state = {};        // { capture, handoff, verify, backend }
let showFront = false; // user chose to look at the captured chat again

init();

async function init() {
  state = await chrome.storage.local.get(["capture", "handoff", "verify", "backend"]);
  if (state.backend) $("backend").value = state.backend;

  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "ping" });
    if (r?.ok) pageSite = r.site;
  } catch { /* not a supported page */ }
  $("site").classList.toggle("live", !!pageSite);
  $("siteText").textContent = pageSite ? `On ${LABELS[pageSite]}` : "Open ChatGPT, Claude or Gemini";

  if (state.handoff?.target) $("target").value = state.handoff.target;
  else suggestTarget();

  render();
  wire();
  tilt();
}

// Default the target to somewhere other than the source chat.
function suggestTarget() {
  const src = state.capture?.site || pageSite;
  if (src && $("target").value === src) $("target").value = src === "claude" ? "chatgpt" : "claude";
}

function wire() {
  $("captureBtn").onclick = capture;
  $("recaptureBtn").onclick = capture;
  $("pasteBtn").onclick = usePaste;
  $("exportBtn").onclick = exportJson;
  $("buildBtn").onclick = build;
  $("insertBtn").onclick = insert;
  $("copyBtn").onclick = () => copy(state.handoff.prompt, "Handoff copied. Paste it into the new chat.");
  $("openBtn").onclick = () => chrome.tabs.create({ url: TARGET_URLS[state.handoff.target] || TARGET_URLS.claude });
  $("rebuildBtn").onclick = () => { showFront = true; render(); };
  $("checkBtn").onclick = check;
  $("copyMissingBtn").onclick = () => copy(
    "Please also follow these rules for the rest of this chat:\n" +
      state.verify.missing.map((r, i) => `${i + 1}. ${r}`).join("\n"),
    "Missing rules copied. Send them in the chat.");
  $("target").onchange = () => {
    // a handoff is formatted for one target; changing it means building again
    if (state.handoff && state.handoff.target !== $("target").value) showFront = true;
    render();
  };
  $("backend").onchange = () => save({ backend: $("backend").value.replace(/\/$/, "") });
}

function render() {
  const c = state.capture, h = state.handoff, v = state.verify;
  const onBack = !!h && !showFront;

  // progress rail
  const step = !c ? 1 : !h ? 2 : 3;
  ["step1", "step2", "step3"].forEach((id, i) => {
    const n = i + 1;
    $(id).className = n < step || (n === 3 && v) ? "done" : n === step ? "now" : "";
  });

  // route
  $("fromName").textContent = c ? c.label : (pageSite ? LABELS[pageSite] : "—");
  $("route").classList.toggle("has-chat", !!c);
  $("route").classList.toggle("ready", onBack);

  // card front
  $("captureBtn").disabled = !pageSite;
  $("emptyState").hidden = !!c;
  $("capturedState").hidden = !c;
  if (c) {
    const when = new Date(c.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("msgCount").textContent = c.messages.length;
    $("captureStatus").textContent =
      `From ${c.label}, captured at ${when}${c.auto ? " when the usage limit appeared" : ""}.`;
    $("buildBtn").textContent = h && showFront ? `Build again for ${LABELS[$("target").value]}` : "Build handoff";
  }
  $("recaptureBtn").disabled = !pageSite;
  $("buildBtn").disabled = !c;
  $("exportBtn").hidden = !c;

  // card back
  $("card").dataset.face = onBack ? "back" : "front";
  $("card").querySelector(".front").inert = onBack;
  $("card").querySelector(".back").inert = !onBack;
  if (h) {
    const s = h.stats;
    $("goal").textContent = h.card.goal;
    $("tRules").textContent = h.card.instructions.length;
    $("tCode").textContent = h.card.artifacts.length;
    $("tSaved").textContent = `${Math.max(0, Math.round((1 - s.compression) * 100))}%`;
    $("insertBtn").disabled = !pageSite;
    $("openBtn").textContent = `Open ${LABELS[h.target] || ""}`.trim();
    $("openBtn").hidden = !TARGET_URLS[h.target];
  }

  // verification
  $("checkPanel").hidden = !h;
  $("checkBtn").disabled = !h || !pageSite;
  $("checkHint").hidden = !!v;
  $("check").hidden = !v;
  if (v) {
    const total = v.checks.length, kept = total - v.missing.length;
    $("meterFill").style.width = total ? `${(kept / total) * 100}%` : "100%";
    $("meterFill").parentElement.classList.toggle("partial", v.missing.length > 0);
    const span = document.createElement("span");
    span.className = v.missing.length ? "bad" : "ok";
    span.textContent = total ? `${kept} of ${total} rules kept` : "No rules to check";
    $("checkSummary").replaceChildren(span, `, goal ${v.goal_correct ? "correct" : "wrong"}`);
    $("missingList").replaceChildren(...v.missing.map((r) => Object.assign(document.createElement("li"), { textContent: r })));
    $("copyMissingBtn").hidden = !v.missing.length;
  }
}

async function capture() {
  try {
    say("Capturing. The page scrolls through the chat; keep this popup open.");
    $("captureBtn").disabled = $("recaptureBtn").disabled = true;
    const r = await chrome.tabs.sendMessage(tab.id, { type: "capture" });
    if (!r?.ok) return say(r?.error || "Capture failed. Reload the page and try again.", true);
    if (!r.messages.length) return say("No messages found on this page. Use Paste a chat instead.", true);
    showFront = false;
    await save({ capture: { site: r.site, label: LABELS[r.site], messages: r.messages, at: Date.now() },
                 handoff: null, verify: null });
    suggestTarget();
    render();
    say("Captured. Pick where to continue, then build the handoff.");
  } catch {
    say("Couldn't reach the page. Reload it and try again.", true);
  } finally {
    render();
  }
}

async function usePaste() {
  const messages = parsePasted($("pasteBox").value);
  if (messages.length < 2) return say('Start each turn with "User:" or "Assistant:".', true);
  showFront = false;
  await save({ capture: { site: "paste", label: LABELS.paste, messages, at: Date.now() }, handoff: null, verify: null });
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
  say("Building the handoff…");
  $("buildBtn").disabled = true;
  try {
    const body = { conversation: { source: state.capture.site, messages: state.capture.messages }, target };
    const res = await api("/handoff", body);
    showFront = false;
    await save({ handoff: { ...res, target }, verify: null });
    passBaton();
    say("Ready. Open the new assistant, then Insert here or Copy.");
  } catch (e) {
    say(e.message, true);
  } finally {
    $("buildBtn").disabled = !state.capture;
  }
}

// The one orchestrated moment: the baton crosses from source to target.
function passBaton() {
  if (REDUCED_MOTION) return;
  const route = $("route");
  route.classList.remove("passing");
  void route.offsetWidth;
  route.classList.add("passing");
}

async function insert() {
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "insert", text: state.handoff.prompt });
    say(r?.ok ? "Inserted. Review it, then press send." : r?.error || "Insert failed. Use Copy instead.", !r?.ok);
  } catch {
    say("Couldn't reach the page. Use Copy instead.", true);
  }
}

async function check() {
  say("Checking the reply…");
  $("checkBtn").disabled = true;
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "lastReply" });
    if (!r?.ok) return say(r?.error || "No reply found on this page yet.", true);
    const res = await api("/verify", { handoff_id: state.handoff.handoff_id, card: state.handoff.card, reply: r.text });
    await save({ verify: res });
    say(res.missing.length ? "Some rules were dropped. Copy them back into the chat." : "Every rule carried over.");
  } catch (e) {
    say(e.message, true);
  } finally {
    $("checkBtn").disabled = !state.handoff || !pageSite;
  }
}

function exportJson() {
  const c = state.capture;
  const blob = new Blob([JSON.stringify({ source: c.site, messages: c.messages }, null, 2)], { type: "application/json" });
  chrome.tabs.create({ url: URL.createObjectURL(blob) });
}

// Subtle 3D: the card leans toward the pointer.
function tilt() {
  if (REDUCED_MOTION) return;
  const stage = document.querySelector(".stage"), card = $("card");
  stage.addEventListener("pointermove", (e) => {
    const r = stage.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    card.classList.add("tilting");
    card.style.setProperty("--ry", `${(x * 8).toFixed(2)}deg`);
    card.style.setProperty("--rx", `${(-y * 6).toFixed(2)}deg`);
  });
  stage.addEventListener("pointerleave", () => {
    card.classList.remove("tilting");
    card.style.setProperty("--ry", "0deg");
    card.style.setProperty("--rx", "0deg");
  });
}

async function api(path, body) {
  const base = ($("backend").value || "http://localhost:8000").replace(/\/$/, "");
  let res;
  try {
    res = await fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error(`Backend not reachable at ${base}. Start it, then try again.`);
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
  catch { say("Copy failed. Select the text and copy it by hand.", true); }
}

function say(text, isError = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", isError);
}

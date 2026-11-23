// Runs inside ChatGPT / Claude / Gemini pages. The popup asks it to read
// the chat, insert the handoff prompt, or read the latest reply.

(() => {
  const site = handoffCurrentSite();
  if (!site) return;
  const adapter = HANDOFF_ADAPTERS[site];

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    (async () => {
      if (msg.type === "ping") return { ok: true, site, label: adapter.label };
      if (msg.type === "capture") return { ok: true, site, messages: await adapter.readMessages() };
      if (msg.type === "insert") return insertText(msg.text);
      if (msg.type === "lastReply") {
        // the latest reply is on screen, so no need to scroll through the chat
        const read = adapter.readVisible || adapter.readMessages;
        const ms = (await read()).filter((m) => m.role === "assistant");
        return ms.length ? { ok: true, text: ms[ms.length - 1].content }
                         : { ok: false, error: "No assistant reply found on this page yet." };
      }
      return { ok: false, error: "unknown request" };
    })().then(reply, (e) => reply({ ok: false, error: String(e) }));
    return true;                      // keep the channel open for the async reply
  });

  function insertText(text) {
    const box = adapter.composer();
    if (!box) return { ok: false, error: "Couldn't find the message box. Use Copy and paste it instead." };
    box.focus();
    let done = false;
    if (box.isContentEditable) {
      // execCommand goes through the editor's own input handling (ProseMirror, Quill)
      done = document.execCommand("insertText", false, text);
    } else if ("value" in box) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(box), "value").set;
      setter.call(box, text);
      done = true;
    }
    box.dispatchEvent(new InputEvent("input", { bubbles: true }));
    return done ? { ok: true } : { ok: false, error: "Insert was blocked. Use Copy and paste it instead." };
  }

  // ---- usage-limit detection -------------------------------------------
  let warned = false;
  let timer = null;
  const observer = new MutationObserver((mutations) => {
    if (warned) return;
    const added = mutations.flatMap((m) => [...m.addedNodes]).filter((n) => n.nodeType === 1);
    if (!added.length) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      const text = added.map((n) => n.textContent || "").join(" ").slice(0, 5000);
      if (HANDOFF_LIMIT_PATTERNS.some((p) => p.test(text))) onLimit();
    }, 800);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  async function onLimit() {
    warned = true;
    observer.disconnect();
    const messages = await adapter.readMessages();
    if (messages.length) {
      await chrome.storage.local.set({
        capture: { site, label: adapter.label, messages, at: Date.now(), auto: true },
      });
    }
    showBanner(messages.length
      ? `Usage limit reached. Handoff saved this chat (${messages.length} messages). Open another assistant and click the Handoff icon to continue.`
      : "Usage limit reached, but Handoff couldn't read this chat. Use the paste option in the Handoff popup.");
  }

  function showBanner(text) {
    const bar = document.createElement("div");
    bar.setAttribute("role", "status");
    bar.style.cssText = "position:fixed;z-index:2147483647;left:50%;bottom:24px;transform:translateX(-50%);" +
      "max-width:560px;padding:12px 40px 12px 16px;border-radius:10px;background:#1f2937;color:#f9fafb;" +
      "font:14px/1.45 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.25)";
    bar.textContent = text;
    const close = document.createElement("button");
    close.textContent = "×";
    close.setAttribute("aria-label", "Dismiss");
    close.style.cssText = "position:absolute;top:6px;right:10px;background:none;border:0;color:inherit;font-size:20px;cursor:pointer";
    close.onclick = () => bar.remove();
    bar.appendChild(close);
    document.body.appendChild(bar);
  }
})();

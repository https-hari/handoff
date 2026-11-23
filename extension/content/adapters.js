// One adapter per site. Sites change their HTML often, so every selector
// lives here. If capture or insert stops working on a site, open DevTools
// on that page, inspect a message and the input box, and update this file.

const HANDOFF_ADAPTERS = {
  chatgpt: {
    label: "ChatGPT",
    url: "https://chatgpt.com/",
    match: (h) => h === "chatgpt.com",
    // ChatGPT only keeps the turns near the screen in the page, so a full
    // capture scrolls through the chat and collects turns as they appear.
    readMessages: () => handoffScrollCollect(chatgptVisible, "[data-conversation-role], [data-message-author-role]"),
    readVisible: chatgptVisible,
    composer: () => document.querySelector("#prompt-textarea, div[contenteditable='true']"),
  },

  claude: {
    label: "Claude",
    url: "https://claude.ai/new",
    match: (h) => h === "claude.ai",
    // Claude also only renders turns near the screen, so scroll and collect.
    readMessages: () => handoffScrollCollect(claudeVisible, CLAUDE_MSG),
    readVisible: claudeVisible,
    composer: () => document.querySelector("div.ProseMirror[contenteditable='true'], div[contenteditable='true']"),
  },

  gemini: {
    label: "Gemini",
    url: "https://gemini.google.com/app",
    match: (h) => h === "gemini.google.com",
    readMessages() {
      return [...document.querySelectorAll("user-query, model-response")]
        .map((el) => ({
          role: el.tagName.toLowerCase() === "user-query" ? "user" : "assistant",
          content: handoffText(el),
        }))
        .filter((m) => m.content);
    },
    composer: () => document.querySelector("rich-textarea [contenteditable='true'], .ql-editor[contenteditable='true']"),
  },
};

// Claude, 2026 layout: [data-testid=user-message] / [data-testid=assistant-message].
// Older layout used .font-claude-response.
const CLAUDE_MSG = "[data-testid='user-message'], [data-testid='assistant-message'], .font-claude-response";
function claudeVisible() {
  return [...document.querySelectorAll(CLAUDE_MSG)]
    .filter((el) => !el.parentElement.closest(CLAUDE_MSG))
    .map((el) => {
      const role = el.matches("[data-testid='user-message']") ? "user" : "assistant";
      const content = handoffText(el);
      // no stable id in the page, so identify a turn by its role and text
      return { key: role + ":" + content.length + ":" + content.slice(0, 200), role, content };
    })
    .filter((m) => m.content);
}

// ChatGPT, 2026 layout: a hidden heading [data-conversation-role] sits inside
// each message's container. Older layout: [data-message-author-role].
function chatgptVisible() {
  const labels = document.querySelectorAll("[data-conversation-role]");
  if (labels.length) {
    return [...labels].map((h, i) => {
      const box = h.parentElement;
      const turn = h.closest("[data-turn-key]");
      const role = h.getAttribute("data-conversation-role") === "user" ? "user" : "assistant";
      return {
        key: (turn ? turn.getAttribute("data-turn-key") : "i" + i) + ":" + role,
        role,
        content: handoffText(box, "[data-conversation-role]"),
      };
    }).filter((m) => m.content);
  }
  return [...document.querySelectorAll("[data-message-author-role]")].map((el, i) => ({
    key: el.getAttribute("data-message-id") || "o" + i,
    role: el.getAttribute("data-message-author-role") === "user" ? "user" : "assistant",
    content: handoffText(el),
  })).filter((m) => m.content);
}

const handoffSleep = (ms) => new Promise((r) => setTimeout(r, ms));

function handoffScroller(el) {
  for (let n = el && el.parentElement; n; n = n.parentElement) {
    const oy = getComputedStyle(n).overflowY;
    if ((oy === "auto" || oy === "scroll") && n.scrollHeight > n.clientHeight + 10) return n;
  }
  return document.scrollingElement;
}

// Scroll through the whole chat and keep every turn seen. ChatGPT mounts
// turns near the viewport and may pull the scroll position back, so each
// position is sampled several times. Turns are ordered by merging each
// on-screen snapshot (which is in page order) into one list.
async function handoffScrollCollect(readVisible, anchorSelector) {
  const anchor = document.querySelector(anchorSelector);
  if (!anchor) return readVisible().map(({ role, content }) => ({ role, content }));
  const sc = handoffScroller(anchor);
  const saved = sc.scrollTop;
  const byKey = new Map();
  const order = [];

  const take = () => {
    const snap = readVisible();
    snap.forEach((m, i) => {
      const old = byKey.get(m.key);
      if (!old || m.content.length > old.content.length) byKey.set(m.key, m);  // keep the fullest copy
      if (order.includes(m.key)) return;
      const prev = snap.slice(0, i).reverse().find((x) => order.includes(x.key));
      const next = snap.slice(i + 1).find((x) => order.includes(x.key));
      if (prev) order.splice(order.indexOf(prev.key) + 1, 0, m.key);
      else if (next) order.splice(order.indexOf(next.key), 0, m.key);
      else order.push(m.key);
    });
  };
  const visit = async (pos) => {
    sc.scrollTop = pos;
    for (let i = 0; i < 6; i++) { await handoffSleep(35); take(); }
  };

  take();
  await visit(0);
  await visit(0);
  for (let pos = 0, i = 0; pos < sc.scrollHeight && i < 500; pos += sc.clientHeight * 0.6, i++) await visit(pos);
  await visit(sc.scrollHeight);
  sc.scrollTop = saved;
  return order.map((k) => byKey.get(k)).map(({ role, content }) => ({ role, content }));
}

// Text of a message with code blocks kept as ``` fences (so the backend can
// copy the latest code verbatim) and math kept as TeX.
function handoffText(el, skipSelector) {
  const clone = el.cloneNode(true);
  if (skipSelector) clone.querySelectorAll(skipSelector).forEach((n) => n.remove());

  // math: KaTeX keeps the original TeX in an <annotation>
  clone.querySelectorAll(".katex-display, .katex").forEach((k) => {
    if (!k.isConnected && !clone.contains(k)) return;
    const tex = k.querySelector("annotation");
    if (!tex) return;
    const display = k.classList.contains("katex-display");
    k.replaceWith(document.createTextNode(display ? `\n$$${tex.textContent}$$\n` : `$${tex.textContent}$`));
  });

  // code: ChatGPT renders code with CodeMirror (one .cm-line per line);
  // other sites use <pre><code class="language-x">
  const fence = (lang, code) => {
    const pre = document.createElement("pre");
    pre.textContent = `\n\`\`\`${lang}\n${code.replace(/\n+$/, "")}\n\`\`\`\n`;
    return pre;
  };
  clone.querySelectorAll("[data-code-block]").forEach((block) => {
    if (!clone.contains(block)) return;
    const cm = block.querySelector(".cm-content");
    if (cm) {
      const lines = [...cm.querySelectorAll(".cm-line")].map((l) => l.textContent);
      block.replaceWith(fence(cm.getAttribute("data-language") || "", lines.join("\n")));
    }
  });
  clone.querySelectorAll("pre").forEach((pre) => {
    if (!clone.contains(pre) || pre.textContent.startsWith("\n```")) return;
    const code = pre.querySelector("code") || pre;
    const lang = ((code.className || "").match(/language-([\w+#.-]+)/) || [])[1] || "";
    pre.replaceWith(fence(lang, code.textContent));
  });
  clone.querySelectorAll("button, svg, [aria-hidden='true']").forEach((n) => n.remove());

  // innerText keeps line breaks between paragraphs and list items, but only
  // works on rendered nodes, so measure the clone off-screen
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-100000px;top:0;width:900px;visibility:visible";
  host.appendChild(clone);
  document.body.appendChild(host);
  const text = clone.innerText;
  host.remove();
  return text.replace(/\n{3,}/g, "\n\n").trim();
}

function handoffCurrentSite() {
  const host = location.hostname;
  for (const [key, a] of Object.entries(HANDOFF_ADAPTERS)) if (a.match(host)) return key;
  return null;
}

// Messages that sites show when you run out of usage.
const HANDOFF_LIMIT_PATTERNS = [
  /usage limit/i,
  /reached (your|the) (message |usage |daily )?limit/i,
  /you['’]ve hit (your|the) .*limit/i,
  /out of (free )?messages/i,
  /limit (resets|will reset)/i,
];

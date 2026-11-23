# Changelog

## 0.2.0 (2026-11-24)

- Redesigned extension popup: Capture → Build → Check progress rail, a From → To route with a baton that
  crosses when the handoff is built, and a state card that flips from the captured chat to the ready handoff
  (goal, rule / code counts, how much smaller), with a subtle 3D tilt.
- Rule check shows a kept/total meter and the missing rules inline.
- Light and dark themes, keyboard focus rings, reduced-motion support.

## 0.1.0 (2026-11-24)

First release.

- State-card extraction: goal, rules, decisions, context, open tasks, dead ends, latest artifacts.
- Code copied verbatim via block stubs; internal block ids scrubbed from text.
- Rolling-window extraction for long chats, oversized messages split; Pydantic validation with retry.
- LLM judge that quotes evidence for each rule; `/verify` and "Check the reply".
- Groq → Gemini fallback, retry-after on rate limits, plain-mode retry when JSON mode fails.
- Chrome extension for ChatGPT, Claude and Gemini, with scroll-through capture for virtualized chats
  and a usage-limit banner that auto-saves the chat.
- Stdlib-only CLI using the clipboard, with optional exact git workspace state (`--repo`, `--test`).
- Eval harness comparing full transcript, tail, summary and state card.

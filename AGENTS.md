# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, Copilot, etc.) working in this repo.
Humans: see [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## What this project is

Handoff moves the **working state** of an AI chat (goal, rules, decisions, latest code, open tasks,
dead ends) from one assistant to another, then checks that the new assistant kept the rules.

Three parts, one backend:

| Part | Path | Tech |
|---|---|---|
| API | `backend/app/` | Python 3.12, FastAPI, Pydantic v2, httpx, SQLite |
| Chrome extension | `extension/` | Manifest V3, plain JavaScript (no build step) |
| CLI | `cli/handoff.py` | Python standard library only |
| Eval harness | `eval/` | Compares full / tail / summary / card |

## Commands

Run from the repo root unless noted.

```bash
# setup (once)
cd backend && python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# tests: no network, no API keys, must pass before any commit
cd backend && python -m pytest -q

# run the API (needs backend/.env with GROQ_API_KEY or GEMINI_API_KEY)
cd backend && uvicorn app.main:app --reload --port 8000

# eval harness, offline plumbing check (no API calls)
python eval/run_eval.py --mock

# real eval (uses API quota; ask the user before running)
python eval/run_eval.py

# CLI end-to-end against a running backend + real LLM (~10 calls; ask first; uses the clipboard, then restores it)
python scripts/e2e_cli.py

# syntax check for extension JS (no bundler)
for f in extension/content/*.js extension/popup/*.js; do node --check "$f"; done
```

## Map of the code

```
backend/app/
  main.py       HTTP endpoints: POST /handoff, POST /verify, GET /stats, GET /health
  extractor.py  chat -> StateCard. Code-block stubs, rolling windows, validate + retry
  render.py     StateCard -> prompt text (XML tags for Claude, Markdown for others)
  verify.py     LLM judge: rule by rule, must quote evidence from the reply
  llm.py        OpenAI-compatible client, Groq -> Gemini fallback, retries
  db.py         SQLite log: handoffs 1:N verifications
  schemas.py    all Pydantic models (the API contract)
backend/tests/  pytest with a FakeLLM; never call a real provider in tests
extension/content/adapters.js   ALL site selectors (ChatGPT, Claude, Gemini) live here
extension/content/content.js    page script: capture, insert, last reply, usage-limit banner
extension/popup/                popup UI
cli/handoff.py                  clipboard + git workspace capture
eval/run_eval.py, eval/dataset/ benchmark with hand-written gold rules
scripts/e2e_cli.py              end-to-end CLI test against a live backend
```

## Invariants: do not break these

1. **Code is never rewritten by the LLM.** Code blocks are replaced by stubs (`[CODE m7b0 | lang | N lines]`)
   before extraction; the model returns only block ids; the backend copies code byte-for-byte.
   Never send full code bodies to the extractor and never let model output replace artifact content.
2. **Block ids are internal.** They must not appear in any text field of a StateCard (`_scrub_ids`).
3. **Every LLM output is validated** (Pydantic + id checks) and retried with the error message, max 2 retries.
4. **Git workspace state is rendered verbatim**, never passed through an LLM.
5. **Tests never hit the network.** Use `FakeLLM` (scripted responses) or monkeypatch `FallbackLLM._call`.
6. **Site selectors stay in `adapters.js`.** content.js and popup must not contain site-specific selectors.
7. **CLI stays standard-library only** (no pip installs needed to run it).
8. Defaults must fit Groq's free tier (8k tokens/minute): `WINDOW_TOKENS=3000`, `MAX_OUTPUT_TOKENS=2048`.

## Conventions

- Python: type hints on public functions, `from __future__ import annotations` in modules, f-strings,
  small functions. Keep comments for *why*, not *what*.
- Errors: providers raise `LLMError` (API returns 502); bad model output raises `ValueError` (API returns 422).
- JavaScript: no frameworks, no build step, `const`/`let`, async/await. Content-script message handlers must
  `return true` and reply asynchronously.
- Any change to `schemas.py` is an API change: update `render.py`, the extension popup and the CLI together.
- New behaviour needs a test in `backend/tests/`. Bug fixes need a test that fails before the fix.
- Keep the README "Design decisions" table in sync when you change how something works.

## When a site breaks capture

ChatGPT, Claude and Gemini change their HTML often, and they only keep messages near the screen in the page
(virtualized). Fix it in `adapters.js` only:
1. Find a stable attribute on each message (`data-*` or `data-testid`, not generated class names).
2. Return `{key, role, content}` from a `*Visible()` function; `key` must be stable across scrolling.
3. Use `handoffScrollCollect(readVisible, anchorSelector)` so the whole chat is collected, not just the screen.
4. Keep code blocks as ``` fences (the backend relies on them) and math as TeX.

## Security and privacy

- Never commit `backend/.env`, API keys, `backend/data/`, or real chat logs. Check `git status` before committing.
- Never print full API keys in logs or output.
- Chats go only to the user's own backend and the LLM provider they configured. Do not add analytics,
  telemetry or third-party calls.
- Extension permissions stay minimal (`storage`, `activeTab`, `clipboardWrite` + the three chat sites and
  localhost). Ask before adding any permission.

## Ask the user before

- Running the real eval or anything else that spends API quota
- Adding a dependency (backend) or any dependency at all (CLI, extension)
- Changing the API contract in `schemas.py`
- Committing, pushing, or rewriting git history

# Handoff

[![CI](https://github.com/https-hari/handoff/actions/workflows/ci.yml/badge.svg)](https://github.com/https-hari/handoff/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Chrome MV3](https://img.shields.io/badge/chrome-MV3-green)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**Hit your usage limit halfway through a task? Continue in another AI without starting over.**

Handoff moves the **working state** of an AI chat (goal, rules, decisions, latest code, what's left,
what already failed) from ChatGPT, Claude or Gemini to another assistant. Then it **checks that the new
assistant actually kept your rules**, and gives you the missing ones to paste back.

It is not a copy-paste of the transcript and not a summary:

| | Copy the whole chat | Plain summary | **Handoff** |
|---|---|---|---|
| Fits a fresh context | often not | yes | yes |
| Keeps every rule you gave | yes | sometimes | yes, and **checked** |
| Code stays exactly as written | yes | rewritten or dropped | **copied byte-for-byte** |
| Knows what already failed | buried | rarely | explicit "don't repeat" list |
| Includes your repo state | no | no | branch, commits, diff, test result (CLI) |

## Results

Benchmark on 4 conversations (coding, API project, study, writing) with hand-written gold rules. A fresh model
gets each context and must restate the goal and rules; an LLM judge scores each rule and must quote evidence
from the reply. Model: `openai/gpt-oss-120b` on Groq.

| How the chat is moved | Rules kept | Goal correct | Avg context tokens |
|---|---|---|---|
| Full transcript | 100% | 100% | 1,213 |
| Last 800 tokens | 0% | 0% | 818 |
| Plain LLM summary | 100% | 75% | 620 |
| **Handoff state card** | **100%** | **100%** | **419** |

Same accuracy as pasting the full chat with **65% fewer tokens**; more reliable than a summary. These test chats
are short, so the full transcript still fits; the gap is expected to grow on long chats. Help wanted: see
[Contributing](CONTRIBUTING.md#most-wanted). Reproduce with `python eval/run_eval.py`.

## How it works

```
Chrome extension / CLI                     FastAPI backend
──────────────────────                     ───────────────
Capture chat (site adapter) ──POST /handoff──►  1. Code blocks → stubs; real code kept aside
                                                2. LLM extracts state card (windowed, validated)
                                                3. Latest code copied back verbatim
Insert prompt in new chat   ◄── prompt ───────  4. Render for target (XML for Claude, Markdown else)

New assistant restates goal + rules
Read its reply              ──POST /verify───►  LLM judge, rule by rule, quoting evidence
Show missing rules          ◄── result ───────  Logged in SQLite

Usage-limit message detected → chat auto-saved, banner shown
```

## Design decisions

| Decision | Why |
|---|---|
| Transfer a **state card**, not the transcript or a summary | A summary loses rules; a transcript may not fit. The card holds what's needed to *continue*. |
| **Code is never rewritten by the LLM** | Code blocks become stubs (`[CODE m7b0 \| python \| 40 lines]`). The model only picks *which* block is latest; the backend copies it byte-for-byte. No hallucinated edits, fewer tokens. |
| **Rolling extraction** for long chats | The chat is split into token windows; the card from window *k* is updated with window *k+1*, so chat length isn't limited by the model's context. |
| **Validated output with retry** | JSON is checked with Pydantic and against real block ids; errors go back to the model (max 2 retries). |
| **Evidence-based judge** | For each rule the judge must quote the line in the reply that states it. No quote = not kept. |
| **Provider fallback** (Groq → Gemini) | When one quota is spent (the exact moment this tool exists for), the next provider is used. Honors `retry-after`. |
| **Repo state rendered verbatim** (CLI `--repo`) | For coding, the truth is on disk. Branch, commits, diff and test output never pass through the LLM. |
| **Scroll-through capture** | ChatGPT and Claude only keep messages near the screen in the page; the extension scrolls and collects every turn in order. |
| **Site adapters in one file** | Sites change their HTML often; all selectors live in `extension/content/adapters.js`. |
| `verifications` table (1:N) | A handoff can be re-checked after missing rules are re-sent; stats use the latest check. |

## Quick start

### 1. Backend
```bash
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add GROQ_API_KEY (free at console.groq.com) and/or GEMINI_API_KEY
uvicorn app.main:app --reload --port 8000
```

### 2. Chrome extension
1. Open `chrome://extensions` and turn on **Developer mode**
2. **Load unpacked** → choose the `extension/` folder → pin the Handoff icon

### 3. Use it
1. On the chat you want to move: Handoff icon → **Capture this chat** (the page scrolls through the chat)
2. Choose where to continue → **Build**
3. Open the new assistant → **Insert here** (or Copy) → send
4. After it replies → **Check the reply** → copy back any missing rules

**Paste a chat instead** in the popup always works if a site changed its layout.

### 4. CLI (desktop apps, IDEs, terminal tools)
Standard library only:
```bash
echo 'alias handoff="python3 ~/path/to/handoff/cli/handoff.py"' >> ~/.zshrc && source ~/.zshrc

handoff build --to claude                        # reads the chat from the clipboard, puts the handoff back
handoff check                                    # after copying the new assistant's reply
handoff build --to claude --repo . --test "pytest -q"   # add exact git state + test result
handoff build --file chat.json                   # use the extension's Export JSON
handoff stats
```
Copying a whole chat from a web page often loses speaker labels and code formatting; the CLI warns when that
happens. For web chats, the extension is more reliable.

## Tests and eval
```bash
cd backend && python -m pytest -q        # no network, no keys
python eval/run_eval.py --mock           # harness check, offline
python eval/run_eval.py                  # real run, uses your API quota
python scripts/e2e_cli.py                # CLI end to end, backend running, ~10 LLM calls
```
Each real run writes `eval/results/results.md`, `results.csv` and `details.json` (full replies and judge
evidence, git-ignored).

## Project layout
```
backend/app/    main.py (API) · extractor.py · render.py · verify.py · llm.py · db.py · schemas.py
backend/tests/  pytest suite with a fake LLM
cli/            handoff.py (stdlib only)
extension/      manifest.json · content/ (site adapters, page script) · popup/
eval/           run_eval.py · dataset/ · results/
scripts/        e2e_cli.py (end-to-end CLI test)
```

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` | | At least one is required |
| `LLM_PROVIDERS` | `groq,gemini` | Order to try providers |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | |
| `GEMINI_MODEL` | `gemini-2.5-flash` | |
| `WINDOW_TOKENS` | `3000` | Chat tokens per extraction pass (fits Groq's free 8k tokens/min) |
| `MAX_OUTPUT_TOKENS` | `2048` | Cap on each model reply |
| `HANDOFF_DB` | `data/handoff.db` | SQLite log |

## Known limits
- Site layouts change; selectors are isolated in one file and may need updating.
- Images and file attachments are not transferred yet, only text, code and math.
- Very long code blocks in ChatGPT may be partly captured (its code viewer only renders visible lines).
- Token counts are estimates (~4 characters per token).
- The benchmark is small (4 chats); more real long chats are needed for strong claims.

## Privacy
Chats go only to your own backend and the LLM provider you configure. Nothing else is sent anywhere; the only
storage is a local SQLite log. See [SECURITY.md](SECURITY.md).

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). AI coding agents: read
[AGENTS.md](AGENTS.md).

## License
[MIT](LICENSE) © 2026 Hari Sharma

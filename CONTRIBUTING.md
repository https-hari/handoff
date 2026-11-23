# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Setup

```bash
git clone https://github.com/https-hari/handoff.git
cd handoff/backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add a GROQ_API_KEY or GEMINI_API_KEY (only needed to run, not to test)
python -m pytest -q         # must pass; tests make no network calls
```

Extension: `chrome://extensions` → Developer mode → **Load unpacked** → choose `extension/`.
After editing extension files, click reload on its card and refresh the chat tab.

## Before you open a pull request

- [ ] `cd backend && python -m pytest -q` passes
- [ ] `python eval/run_eval.py --mock` runs
- [ ] If you touched the CLI or the API: `python scripts/e2e_cli.py` passes (backend running)
- [ ] `node --check` passes for every file in `extension/content/` and `extension/popup/`
- [ ] New behaviour has a test; bug fixes have a test that failed before the fix
- [ ] No keys, `.env`, `backend/data/` or real chat logs in the diff
- [ ] README updated if behaviour or setup changed

## Most wanted

- **A chat site changed its layout** and capture stopped working: fix the selectors in
  `extension/content/adapters.js` (see "When a site breaks capture" in [AGENTS.md](AGENTS.md)).
  Open a "Site broke" issue first if you can't fix it yourself.
- **Eval data**: long, real conversations with hand-written gold rules. Remove anything personal first.
  Format: see any file in `eval/dataset/`.
- New target sites (Perplexity, DeepSeek, Copilot): add an adapter, no other files should need to change.

## Style

- Python: type hints, small functions, comments explain *why*.
- JavaScript: no frameworks or build step.
- Commit messages: imperative, short subject (`Fix ChatGPT capture after layout change`), body if needed.

By contributing you agree your work is released under the [MIT License](LICENSE) and that you follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

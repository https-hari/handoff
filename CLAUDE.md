# CLAUDE.md

The project instructions for all coding agents are in AGENTS.md. Read and follow them:

@AGENTS.md

## Notes for Claude Code

- Start by running the tests (`cd backend && python -m pytest -q`) so you know the baseline is green.
- Use plan mode for anything that touches `schemas.py`, the extractor's prompt, or more than one of
  backend / extension / CLI at once.
- After changing `extension/`, tell the user to click reload on `chrome://extensions` and refresh the chat tab;
  you cannot test the extension from the terminal.
- Prefer small, reviewable diffs. Show the diff and wait for approval before committing.

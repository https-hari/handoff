# Security and privacy

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository
(<https://github.com/https-hari/handoff/security/advisories/new>).
You'll get a reply within 7 days.

## How your data moves

- The extension and CLI send a chat **only to your own backend** (default `http://localhost:8000`).
- The backend sends it to **the LLM provider you configured** (Groq and/or Gemini) for extraction and checking.
  Their data policies apply to that request.
- The backend stores a log in local SQLite (`backend/data/handoff.db`): the state card, prompt and check results.
  Delete that file to clear it.
- No analytics, telemetry or other third-party calls.

## Keeping keys safe

- Keys live only in `backend/.env`, which is git-ignored. `backend/.env.example` must stay blank.
- If a key was ever committed, revoke it at the provider immediately; deleting the commit is not enough.
- The API accepts requests only from the extension and `localhost` (CORS). Don't expose port 8000 to a network.

## Extension permissions

`storage`, `activeTab`, `clipboardWrite`, plus access to chatgpt.com, claude.ai, gemini.google.com and
localhost:8000. Nothing else.

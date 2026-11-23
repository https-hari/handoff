#!/usr/bin/env python3
"""Handoff CLI: works with any AI app through the clipboard.

  handoff build [--to claude] [--repo .] [--test "pytest -q"] [--file chat.txt]
      Read a chat (clipboard by default), build the handoff, copy it back
      to the clipboard. --repo adds exact git state for coding work.

  handoff check
      Copy the new assistant's reply, then run this. Shows which rules it
      kept and copies a message with the missing ones.

  handoff stats
      Compression and rule retention across all handoffs.

Standard library only, Python 3.8+. Talks to the backend at
$HANDOFF_URL (default http://localhost:8000).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

STATE_DIR = Path(os.environ.get("HANDOFF_HOME", Path.home() / ".handoff"))
DIFF_MAX_LINES = 300
TEST_OUTPUT_LINES = 30

# Labels that apps put in front of turns when you copy a whole chat.
ROLE_LINE = re.compile(
    r"^\s*(?P<label>you said|you|user|me|human|"
    r"chatgpt said|chatgpt|claude said|claude|gemini said|gemini|assistant|ai|model)\s*:?\s*$"
    r"|^\s*(?P<inline>you|user|me|human|chatgpt|claude|gemini|assistant|ai|model)\s*:\s+(?P<rest>.*)$",
    re.IGNORECASE,
)
USER_LABELS = {"you said", "you", "user", "me", "human"}


# ---------------------------------------------------------------- clipboard
def read_clipboard() -> str:
    system = platform.system()
    if system == "Darwin":
        cmd = ["pbpaste"]
    elif system == "Windows":
        cmd = ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]
    elif shutil.which("wl-paste"):
        cmd = ["wl-paste", "--no-newline"]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def write_clipboard(text: str) -> None:
    system = platform.system()
    if system == "Darwin":
        cmd = ["pbcopy"]
    elif system == "Windows":
        cmd = ["clip"]
    elif shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    else:
        cmd = ["xclip", "-selection", "clipboard"]
    subprocess.run(cmd, input=text, text=True, check=True)


# ------------------------------------------------------------- chat parsing
def parse_chat(text: str) -> list[dict]:
    """Split copied chat text into turns using role labels.

    Handles both 'User: hi' on one line and a label on its own line
    (e.g. 'You said:' followed by the message, as ChatGPT copies). If no
    labels are found, the whole text is sent as one block; the extractor
    still works, it just can't tell who said what.
    """
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)                        # Export JSON from the extension
        msgs = data["messages"] if isinstance(data, dict) else data
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    turns: list[dict] = []
    for line in text.splitlines():
        m = ROLE_LINE.match(line)
        if m:
            label = (m.group("label") or m.group("inline")).lower()
            role = "user" if label in USER_LABELS else "assistant"
            turns.append({"role": role, "content": m.group("rest") or ""})
        elif turns:
            turns[-1]["content"] += "\n" + line
        else:
            turns.append({"role": "user", "content": line})
    turns = [{"role": t["role"], "content": t["content"].strip()} for t in turns]
    turns = [t for t in turns if t["content"]]
    if len(turns) < 2:
        return [{"role": "user", "content":
                 "Transcript of my previous chat (speaker labels were not preserved):\n\n" + text}]
    return turns


# ------------------------------------------------------------- repo state
def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def collect_workspace(repo: Path, test_cmd: Optional[str] = None) -> dict:
    _git(repo, "rev-parse", "--is-inside-work-tree")
    ws: dict = {
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "recent_commits": [], "changed_files": [],
    }
    try:
        ws["recent_commits"] = _git(repo, "log", "-5", "--oneline").strip().splitlines()
    except RuntimeError:
        pass                                           # repo with no commits yet
    ws["changed_files"] = [l for l in _git(repo, "status", "--porcelain").splitlines() if l.strip()]

    try:
        diff = _git(repo, "diff", "HEAD")
    except RuntimeError:
        diff = _git(repo, "diff")
    lines = diff.splitlines()
    ws["diff_truncated"] = len(lines) > DIFF_MAX_LINES
    ws["diff"] = "\n".join(lines[:DIFF_MAX_LINES]) or None

    if test_cmd:
        r = subprocess.run(test_cmd, shell=True, cwd=repo, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip().splitlines()
        ws.update(test_command=test_cmd, test_exit_code=r.returncode,
                  test_output="\n".join(out[-TEST_OUTPUT_LINES:]))
    return ws


# --------------------------------------------------------------------- api
def api(path: str, body: Optional[dict] = None) -> dict:
    base = os.environ.get("HANDOFF_URL", "http://localhost:8000").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        raise SystemExit(f"Backend error ({e.code}): {detail}")
    except urllib.error.URLError:
        raise SystemExit(f"Backend not reachable at {base}. Start it with: uvicorn app.main:app --port 8000")


def _save_last(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "last.json").write_text(json.dumps(data))


def _load_last() -> dict:
    p = STATE_DIR / "last.json"
    if not p.exists():
        raise SystemExit("No handoff yet. Run: handoff build")
    return json.loads(p.read_text())


# ---------------------------------------------------------------- commands
def cmd_build(args, call: Callable = api, read: Callable = read_clipboard,
              write: Callable = write_clipboard) -> int:
    text = Path(args.file).read_text() if args.file else read()
    if not text.strip():
        raise SystemExit("Clipboard is empty. Copy the chat first (select all in the app, then copy).")
    messages = parse_chat(text)
    body = {"conversation": {"source": "cli", "messages": messages}, "target": args.to}
    if args.repo:
        repo = Path(args.repo).expanduser().resolve()
        try:
            body["workspace"] = collect_workspace(repo, args.test)
        except RuntimeError as e:
            raise SystemExit(f"--repo {repo} is not a usable git repository: {e}")

    print(f"Read {len(messages)} message(s). Building handoff…", file=sys.stderr)
    res = call("/handoff", body)
    _save_last({"handoff_id": res["handoff_id"], "card": res["card"], "prompt": res["prompt"]})
    write(res["prompt"])

    s, card = res["stats"], res["card"]
    print(f"Copied to clipboard: {s['prompt_tokens']:,} tokens (chat was {s['source_tokens']:,}).")
    print(f"Goal: {card['goal']}")
    print(f"{len(card['instructions'])} rules · {len(card['artifacts'])} code block(s)"
          + (" · repo state included" if args.repo else ""))
    if len(messages) == 1:
        print("Note: no speaker labels found in the copied text, so the chat was sent as one block.\n"
              "      Rules and code may be missed. For best results use the Chrome extension or\n"
              "      its Export JSON, then: handoff build --file chat.json", file=sys.stderr)
    if not card["instructions"]:
        print("Note: no standing rules were found in this chat, so 'handoff check' will only check the goal.",
              file=sys.stderr)
    print("Paste it into the new assistant. After it replies, copy the reply and run: handoff check")
    return 0


def cmd_check(args, call: Callable = api, read: Callable = read_clipboard,
              write: Callable = write_clipboard) -> int:
    last = _load_last()
    reply = Path(args.file).read_text() if args.file else read()
    if not reply.strip():
        raise SystemExit("Clipboard is empty. Copy the new assistant's reply first.")
    if last.get("prompt") and reply.strip() == last["prompt"].strip():
        raise SystemExit("The clipboard still holds the handoff itself. Paste it into the new assistant,\n"
                         "wait for its reply, copy that reply, then run: handoff check")
    res = call("/verify", {"handoff_id": last["handoff_id"], "card": last["card"], "reply": reply})
    total = len(res["checks"])
    print(f"{total - len(res['missing'])}/{total} rules kept · goal {'correct' if res['goal_correct'] else 'WRONG'}")
    if res["missing"]:
        print("Missing:")
        for r in res["missing"]:
            print(f"  - {r}")
        write("Please also follow these rules for the rest of this chat:\n"
              + "\n".join(f"{i}. {r}" for i, r in enumerate(res["missing"], 1)))
        print("A message with the missing rules is on your clipboard. Paste it into the chat.")
    return 0 if not res["missing"] else 1


def cmd_stats(args, call: Callable = api, **_) -> int:
    print(json.dumps(call("/stats"), indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="handoff", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a handoff from the clipboard (or --file)")
    b.add_argument("--to", default="generic", choices=["claude", "chatgpt", "gemini", "generic"])
    b.add_argument("--file", help="read the chat from a file instead of the clipboard")
    b.add_argument("--repo", help="include git state of this repo (branch, commits, diff)")
    b.add_argument("--test", help='command to run in the repo and include, e.g. "pytest -q"')
    b.set_defaults(fn=cmd_build)

    c = sub.add_parser("check", help="check the new assistant's reply from the clipboard (or --file)")
    c.add_argument("--file")
    c.set_defaults(fn=cmd_check)

    sub.add_parser("stats", help="show overall stats").set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    if args.cmd == "build" and args.test and not args.repo:
        ap.error("--test needs --repo")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

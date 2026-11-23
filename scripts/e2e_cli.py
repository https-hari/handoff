#!/usr/bin/env python3
"""End-to-end test of the Handoff CLI against a RUNNING backend and a real LLM.

Unlike backend/tests (fake LLM, no network), this runs the real `cli/handoff.py`
as a subprocess, talks to the backend over HTTP, and uses your API quota
(about 10 LLM calls). Your clipboard is used, then restored.

    # terminal 1
    cd backend && uvicorn app.main:app --port 8000
    # terminal 2
    python scripts/e2e_cli.py

Exit code 0 = every check passed.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / "cli" / "handoff.py")]
URL = os.environ.get("HANDOFF_URL", "http://localhost:8000")

FENCE = "`" * 3
V1 = f"""{FENCE}python
import heapq

def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    heap = list(intervals)
    heapq.heapify(heap)
    return heap
{FENCE}"""
V2 = f"""{FENCE}python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    for start, end in intervals:
        if start > end:
            raise ValueError(f"Invalid interval [{{start}}, {{end}}]")
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
{FENCE}"""

# Same layout ChatGPT gives when you copy a chat: label on its own line.
CHAT_LABELED = f"""You said:
Write a Python function to merge overlapping intervals. Rules for this whole chat: Python 3.12 only, no external libraries, type hints everywhere, and state the time complexity after every function.
ChatGPT said:
Here is a heap version.
{V1}
Time complexity: O(n log n).
You said:
That's worse. Go back to sorting, drop the heap idea. Also add input validation: raise ValueError if any interval has start > end.
ChatGPT said:
Sorting version with validation:
{V2}
Time complexity: O(n log n).
You said:
Next I want unit tests with pytest, but don't write them yet.
ChatGPT said:
Understood. Tell me when you want the tests.
"""

CHAT_EXPORT = {"source": "chatgpt", "messages": [
    {"role": "user", "content": "Help me write a cold email for an SDE internship. Rules: under 120 words, "
                                "no buzzwords like 'passionate', always end with a specific question."},
    {"role": "assistant", "content": "Draft 1: Hi Priya, ... Would a 15-minute call next week work?"},
    {"role": "user", "content": "Make it formal."},
    {"role": "assistant", "content": "Formal draft: Dear Ms. Priya, ..."},
    {"role": "user", "content": "Actually forget the formal tone, make it casual and direct."},
    {"role": "assistant", "content": "Casual draft: Hey Priya, ... Could we talk Thursday?"},
]}



def reply_with(card: dict, rules: list[str]) -> str:
    """What a well-behaved new assistant answers to the handoff's check question."""
    lines = "\n".join(f"   {i}. {r}" for i, r in enumerate(rules, 1))
    return f"1. Goal: {card['goal']}\n2. Rules I will follow:\n{lines}"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def run(*args: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([*CLI, *args], capture_output=True, text=True, timeout=600,
                          env={**os.environ, **(env or {})}, cwd=cwd)


def clip_read() -> str:
    cmd = ["pbpaste"] if platform.system() == "Darwin" else ["xclip", "-selection", "clipboard", "-o"]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def clip_write(text: str) -> None:
    cmd = ["pbcopy"] if platform.system() == "Darwin" else ["xclip", "-selection", "clipboard"]
    subprocess.run(cmd, input=text, text=True, check=True)


def last(home: Path) -> dict:
    return json.loads((home / "last.json").read_text())


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "e2e@example.com")
    g("config", "user.name", "e2e")
    (repo / "intervals.py").write_text("def merge_intervals(intervals):\n    return intervals\n")
    g("add", ".")
    g("commit", "-qm", "Add merge_intervals stub")
    (repo / "intervals.py").write_text("def merge_intervals(intervals):\n    return sorted(intervals)\n")
    return repo


def main() -> int:
    try:
        with urllib.request.urlopen(URL + "/health", timeout=5) as r:
            print(f"Backend: {URL} -> {json.loads(r.read())}")
    except OSError:
        print(f"Backend not reachable at {URL}. Start it first: cd backend && uvicorn app.main:app --port 8000")
        return 2

    saved_clipboard = clip_read()
    tmp = Path(tempfile.mkdtemp(prefix="handoff-e2e-"))
    home = tmp / "state"
    env = {"HANDOFF_HOME": str(home)}
    try:
        print("\n1. build from the clipboard (ChatGPT-style labels, two code versions)")
        clip_write(CHAT_LABELED)
        p = run("build", "--to", "claude", env=env)
        check("exit code 0", p.returncode == 0, p.stderr[-300:])
        check("read 6 messages", "Read 6 message(s)" in p.stderr, p.stderr[:200])
        if p.returncode == 0:
            card, prompt = last(home)["card"], last(home)["prompt"]
            low = prompt.lower()
            check("handoff copied to clipboard", clip_read().strip() == prompt.strip())
            check("Claude XML format", "<handoff>" in prompt and "</handoff>" in prompt)
            check("found >= 4 rules", len(card["instructions"]) >= 4, str(card["instructions"]))
            for kw in ("3.12", "external", "type hint", "complexity"):
                check(f"rule kept: {kw}", kw in low)
            code = "\n".join(a["content"] for a in card["artifacts"])
            check("latest code copied exactly", V2.split("\n", 1)[1].rsplit(FENCE, 1)[0].strip() in code)
            check("old heap version not used", "heapq" not in code)
            check("pytest listed as still to do", "pytest" in " ".join(card.get("open_tasks", [])).lower())
            check("no internal block ids leaked", not any(f"m{i}b0" in json.dumps(
                {k: v for k, v in card.items() if k != "artifacts"}) for i in range(6)))

        print("\n2. check: reply that keeps every rule")
        card = last(home)["card"]
        f = tmp / "good.txt"
        f.write_text(reply_with(card, card["instructions"]))
        p = run("check", "--file", str(f), env=env)
        n = len(last(home)["card"]["instructions"]) if (home / "last.json").exists() else 0
        check("exit code 0", p.returncode == 0, p.stdout + p.stderr[-300:])
        check(f"{n}/{n} rules kept", f"{n}/{n} rules kept" in p.stdout, p.stdout.strip())
        check("goal correct", "goal correct" in p.stdout, p.stdout.strip())

        print("\n3. check: reply that drops rules")
        f = tmp / "bad.txt"
        f.write_text(reply_with(card, card["instructions"][:1]))
        p = run("check", "--file", str(f), env=env)
        check("exit code 1", p.returncode == 1, p.stdout.strip())
        check("lists missing rules", "Missing:" in p.stdout, p.stdout.strip())
        check("missing rules copied to clipboard", clip_read().startswith("Please also follow"))

        print("\n4. check refuses when the reply is the handoff itself")
        f = tmp / "same.txt"
        f.write_text(last(home)["prompt"])
        p = run("check", "--file", str(f), env=env)
        check("refused with a clear message", p.returncode != 0 and "still holds the handoff" in p.stderr,
              p.stderr.strip())

        print("\n5. build from the extension's Export JSON (a revoked rule)")
        f = tmp / "chat.json"
        f.write_text(json.dumps(CHAT_EXPORT))
        p = run("build", "--to", "gemini", "--file", str(f), env=env)
        check("exit code 0", p.returncode == 0, p.stderr[-300:])
        if p.returncode == 0:
            card = last(home)["card"]
            rules = " ".join(card["instructions"]).lower()
            check("read 6 messages", "Read 6 message(s)" in p.stderr)
            check("rule kept: 120 words", "120" in rules, rules)
            check("revoked 'formal' rule dropped", "formal" not in rules or "casual" in rules, rules)
            check("Markdown format for Gemini", "<handoff>" not in last(home)["prompt"])

        print("\n6. build with --repo and --test (exact git state)")
        repo = make_repo(tmp)
        f = tmp / "chat.txt"
        f.write_text(CHAT_LABELED)
        p = run("build", "--to", "chatgpt", "--file", str(f), "--repo", str(repo),
                "--test", f'"{sys.executable}" -c "print(\'3 passed\')"', env=env)
        check("exit code 0", p.returncode == 0, p.stderr[-300:])
        if p.returncode == 0:
            prompt = last(home)["prompt"]
            check("workspace section added", "Workspace state (from git, exact)" in prompt)
            check("branch shown", "main" in prompt)
            check("last commit shown", "Add merge_intervals stub" in prompt)
            check("diff included verbatim", "+    return sorted(intervals)" in prompt)
            check("test result included", "3 passed" in prompt and "passed" in prompt)

        print("\n7. warnings and errors")
        f = tmp / "nolabels.txt"
        f.write_text("merge intervals in python, sorted approach, use type hints\nthen add validation")
        p = run("build", "--file", str(f), env=env)
        check("warns when speaker labels are missing", "no speaker labels" in p.stderr, p.stderr[-300:])
        p = run("build", "--test", "pytest", env=env)
        check("--test without --repo is rejected", p.returncode != 0 and "--test needs --repo" in p.stderr)
        p = run("build", "--file", str(f), "--repo", str(tmp), env=env)
        check("--repo on a non-git folder fails clearly",
              p.returncode != 0 and "not a usable git repository" in p.stderr, p.stderr[-200:])
        p = run("stats", env={**env, "HANDOFF_URL": "http://localhost:9"})
        check("unreachable backend gives a clear message", "not reachable" in p.stderr, p.stderr.strip())

        print("\n8. stats")
        p = run("stats", env=env)
        try:
            s = json.loads(p.stdout)
            check("stats returns counts", s.get("handoffs", 0) >= 3, p.stdout.strip())
        except ValueError:
            check("stats returns JSON", False, p.stdout + p.stderr)
    finally:
        clip_write(saved_clipboard)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

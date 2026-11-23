"""Compare ways of moving a chat to a new model.

For every conversation in eval/dataset and every method:
  1. build the context the new model receives
  2. ask the new model to restate the goal and the rules it will follow
  3. a judge checks each GOLD rule (written by hand in the dataset file)
     and whether the goal is right

Methods:
  full     whole transcript pasted (upper bound, often too big in practice)
  tail     only the last --tail-tokens of the chat (what fits in a small context)
  summary  a plain LLM summary of the chat
  card     Handoff state card (this project)

Usage:
  python eval/run_eval.py                 # real run, needs API keys in backend/.env
  python eval/run_eval.py --mock          # plumbing test, no API calls, numbers meaningless
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from dotenv import load_dotenv  # noqa: E402

from app.extractor import extract_state  # noqa: E402
from app.llm import FallbackLLM, estimate_tokens, providers_from_env  # noqa: E402
from app.render import CHECK, render_prompt  # noqa: E402
from app.schemas import Conversation, StateCard  # noqa: E402
from app.verify import verify_reply  # noqa: E402

PROBE = ("You are taking over this work in a new chat. Before continuing, reply with ONLY: "
         "(1) the goal in one line, and (2) a numbered list of every rule you must follow.")
METHODS = ["full", "tail", "summary", "card"]


def transcript(conv: Conversation) -> str:
    return "\n\n".join(f"{m.role.upper()}: {m.content}" for m in conv.messages)


def tail(text: str, tokens: int) -> str:
    chars = tokens * 4
    return text if len(text) <= chars else "...[earlier messages cut]...\n" + text[-chars:]


def build_context(method: str, conv: Conversation, llm, tail_tokens: int) -> str:
    t = transcript(conv)
    if method == "full":
        return "Previous conversation:\n\n" + t
    if method == "tail":
        return "Previous conversation (most recent part):\n\n" + tail(t, tail_tokens)
    if method == "summary":
        s = llm.complete([{"role": "user", "content":
                           "Summarize this conversation so another assistant can continue it.\n\n" + t}])
        return "Summary of the previous conversation:\n\n" + s
    if method == "card":
        card, _ = extract_state(llm, conv)
        return render_prompt(card, "generic").replace(CHECK, "").strip()
    raise ValueError(method)


def run(llm, judge, tail_tokens: int, methods: list[str], ids=None, details=None) -> list[dict]:
    rows = []
    for path in sorted((ROOT / "dataset").glob("*.json")):
        item = json.loads(path.read_text())
        if ids and item["id"] not in ids:
            continue
        conv = Conversation(source="eval", messages=item["messages"])
        gold = StateCard(goal=item["gold"]["goal"], instructions=item["gold"]["instructions"])
        for method in methods:
            ctx = build_context(method, conv, llm, tail_tokens)
            reply = llm.complete([{"role": "user", "content": f"{ctx}\n\n{PROBE}"}], temperature=0.0)
            raw: list[str] = []
            v = verify_reply(judge, gold, reply, raw)
            if details is not None:
                details.append({"id": item["id"], "method": method, "context": ctx, "reply": reply,
                                "judge_raw": raw,
                                "checks": [c.model_dump() for c in v.checks], "goal_correct": v.goal_correct})
            rows.append({"id": item["id"], "task": item.get("task", ""), "method": method,
                         "context_tokens": estimate_tokens(ctx), "retention": round(v.retention, 3),
                         "goal_correct": int(v.goal_correct), "missing": " | ".join(v.missing)})
            print(f"{item['id']:<16} {method:<8} retention={v.retention:.2f} "
                  f"goal={int(v.goal_correct)} tokens={estimate_tokens(ctx)}")
    return rows


def summarize(rows: list[dict], methods: list[str]) -> str:
    lines = ["| Method | Rules kept | Goal correct | Avg context tokens |",
             "|---|---|---|---|"]
    for m in methods:
        r = [x for x in rows if x["method"] == m]
        if not r:
            continue
        ret = sum(x["retention"] for x in r) / len(r)
        goal = sum(x["goal_correct"] for x in r) / len(r)
        tok = sum(x["context_tokens"] for x in r) / len(r)
        lines.append(f"| {m} | {ret:.0%} | {goal:.0%} | {tok:,.0f} |")
    return "\n".join(lines)


class MockLLM:
    """Offline stand-in to test the harness. Behaves crudely on purpose:
    it 'remembers' only rule-like sentences that are present in its input."""
    RULE = re.compile(r"[^.\n]*\b(only|never|always|do not|don't|no |must|max|under|use)\b[^.\n]*", re.I)

    def complete(self, messages, json_mode=False, temperature=0.1):
        sys_msg = messages[0]["content"] if messages[0]["role"] == "system" else ""
        text = messages[-1]["content"]
        if "WORKING STATE" in sys_msg:                       # extractor
            rules = [m.group(0).strip() for m in self.RULE.finditer(text.split("[1]")[0])]
            return json.dumps({"goal": "Continue the user's task", "instructions": rules[:8],
                               "decisions": [], "context": [], "open_tasks": [], "dead_ends": [],
                               "artifact_ids": []})
        if "took over a task" in sys_msg:                    # judge
            expected = re.findall(r"^\d+\. (.+)$", text.split("ASSISTANT REPLY:")[0], re.M)
            reply = text.split("ASSISTANT REPLY:")[1].lower()
            kept = [all(w in reply for w in re.findall(r"[a-z0-9+]{4,}", e.lower())[:2]) for e in expected]
            return json.dumps({"goal_correct": True, "kept": kept})
        if text.startswith("Summarize"):
            return text[:600] + " ... " + text[-600:]
        return "\n".join(m.group(0) for m in self.RULE.finditer(text))   # target model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="offline plumbing test")
    ap.add_argument("--tail-tokens", type=int, default=800)
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--ids", help="only these dataset ids, comma-separated")
    args = ap.parse_args()
    methods = args.methods.split(",")

    if args.mock:
        llm = judge = MockLLM()
    else:
        load_dotenv(ROOT.parent / "backend" / ".env")
        llm = judge = FallbackLLM(providers_from_env())

    details: list[dict] = []
    ids = set(args.ids.split(",")) if args.ids else None
    rows = run(llm, judge, args.tail_tokens, methods, ids, details)
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    suffix = "_mock" if args.mock else ""
    # full replies, so a bad score can be checked by reading what the model actually said
    (out / f"details{suffix}.json").write_text(json.dumps(details, indent=2, ensure_ascii=False))
    with open(out / f"results{suffix}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    table = summarize(rows, methods)
    (out / f"results{suffix}.md").write_text(
        ("> MOCK RUN: numbers test the harness only.\n\n" if args.mock else "") + table + "\n")
    print("\n" + table)


if __name__ == "__main__":
    main()

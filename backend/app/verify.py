"""Check whether the new model actually kept the handed-over rules.

After the handoff prompt is sent, the new model restates the goal and
rules. An LLM judge compares that reply against the card, rule by rule.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from .llm import LLM, parse_json
from .schemas import InstructionCheck, StateCard, VerifyResponse

JUDGE = (
    "You check whether an AI assistant correctly took over a task. You get the expected "
    "goal, the expected rules (numbered) and the assistant's reply.\n"
    "For EACH expected rule, find the part of the reply that states it. Paraphrases, synonyms "
    "and stricter versions count as kept: 'Always answer in bullet points, max 5 bullets' keeps "
    "both 'Answer in bullet points' and 'Use at most 5 bullets'. One sentence in the reply can "
    "keep several rules. A rule is missing only if nothing in the reply states it, the reply "
    "states a weaker version (e.g. 'keep it short' for 'under 1000 words'), or the reply "
    "contradicts it.\n"
    "goal_correct is true if the reply's goal is the same task as the expected goal, even if "
    "worded differently or with more detail.\n"
    "Return ONLY JSON: {\"goal_correct\": true|false, \"rules\": [{\"n\": 1, "
    "\"evidence\": \"short quote from the reply, or empty\", \"kept\": true|false}, ...]} "
    "with one entry per expected rule, in order."
)


class _Rule(BaseModel):
    n: int
    evidence: str = ""
    kept: bool


class _Judged(BaseModel):
    goal_correct: bool
    rules: list[_Rule] = []
    kept: list[bool] = []          # older, shorter format; still accepted

    def kept_flags(self) -> list[bool]:
        if self.rules:
            # quoting evidence first keeps the judge honest; no quote means not kept
            return [r.kept and bool(r.evidence.strip()) for r in sorted(self.rules, key=lambda r: r.n)]
        return self.kept


def verify_reply(llm: LLM, card: StateCard, reply: str, raw: list | None = None) -> VerifyResponse:
    """raw: if a list is given, the judge's raw outputs are appended (for debugging evals)."""
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(card.instructions, 1)) or "(none)"
    messages = [
        {"role": "system", "content": JUDGE},
        {"role": "user", "content": f"EXPECTED GOAL: {card.goal}\n\nEXPECTED RULES:\n{rules}"
                                    f"\n\nASSISTANT REPLY:\n{reply}"},
    ]
    judged = None
    for _ in range(3):
        text = llm.complete(messages, json_mode=True, temperature=0.0)
        if raw is not None:
            raw.append(text)
        try:
            judged = _Judged.model_validate(parse_json(text))
            if len(judged.kept_flags()) != len(card.instructions):
                raise ValueError(f"expected {len(card.instructions)} entries in 'rules', "
                                 f"got {len(judged.kept_flags())}")
            break
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            judged = None
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": f"Invalid: {e}. Return the JSON only."}]
    if judged is None:
        raise ValueError("judge returned invalid output")

    checks = [InstructionCheck(instruction=r, kept=k)
              for r, k in zip(card.instructions, judged.kept_flags())]
    missing = [c.instruction for c in checks if not c.kept]
    total = len(checks)
    return VerifyResponse(goal_correct=judged.goal_correct, checks=checks, missing=missing,
                          retention=(total - len(missing)) / total if total else 1.0)

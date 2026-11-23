"""Turn a conversation into a validated StateCard.

Design choices (good interview talking points):
1. Code blocks are collapsed to short stubs before the model sees the
   chat. The model only picks WHICH block is the latest version (by id);
   the code itself is copied verbatim afterwards, so it can't be altered.
2. Long chats are processed in windows. The card from window k is passed
   into window k+1 and updated (rolling extraction), so chat length is
   not limited by the model's context window.
3. Output is validated with Pydantic. On failure the error is sent back
   to the model and it retries (max 2 times).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .llm import LLM, estimate_tokens, parse_json
from .schemas import Artifact, Conversation, StateCard

BLOCK_ID = re.compile(
    r"(?:\bthe\s+)?(?:\b(?:version|block|code|snippet)\s+)?\[?(?:CODE\s+)?\bm\d+b\d+\b\]?",
    re.IGNORECASE)
FENCE = re.compile(r"```([\w+#.-]*)[^\n]*\n(.*?)```", re.DOTALL)
MAX_RETRIES = 2


@dataclass
class CodeBlock:
    block_id: str
    language: str | None
    content: str


def index_code_blocks(conv: Conversation) -> tuple[list[str], dict[str, CodeBlock]]:
    """Replace each fenced code block with a stub and remember the original."""
    blocks: dict[str, CodeBlock] = {}
    rendered = []
    for i, msg in enumerate(conv.messages):
        counter = 0

        def stub(m: re.Match) -> str:
            nonlocal counter
            bid = f"m{i}b{counter}"
            counter += 1
            lang = m.group(1) or None
            code = m.group(2).rstrip("\n")
            blocks[bid] = CodeBlock(bid, lang, code)
            lines = code.split("\n")
            preview = "\n".join(lines[:3])
            tail = f"\n...\n{lines[-1]}" if len(lines) > 4 else ""
            return (f"[CODE {bid} | {lang or 'text'} | {len(lines)} lines]\n"
                    f"{preview}{tail}\n[/CODE {bid}]")

        body = FENCE.sub(stub, msg.content)
        rendered.append(f"[{i}] {msg.role.upper()}: {body}")
    return rendered, blocks


def _split_long(text: str, budget_tokens: int) -> list[str]:
    """Cut one oversized message into parts at line breaks (code stubs are short, so safe)."""
    max_chars = budget_tokens * 4
    parts, cur = [], ""
    for ln in text.split("\n"):
        while len(ln) > max_chars:                       # a single giant line
            if cur:
                parts.append(cur); cur = ""
            parts.append(ln[:max_chars]); ln = ln[max_chars:]
        if cur and len(cur) + len(ln) + 1 > max_chars:
            parts.append(cur); cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        parts.append(cur)
    return parts


def split_windows(lines: list[str], budget_tokens: int) -> list[list[str]]:
    """Group whole messages into windows that fit the token budget.
    A message bigger than the budget is cut at line breaks."""
    expanded = []
    for line in lines:
        expanded.extend(_split_long(line, budget_tokens) if estimate_tokens(line) > budget_tokens
                        else [line])
    windows, current, used = [], [], 0
    for line in expanded:
        t = estimate_tokens(line)
        if current and used + t > budget_tokens:
            windows.append(current)
            current, used = [], 0
        current.append(line)
        used += t
    if current:
        windows.append(current)
    return windows


SCHEMA_HINT = """{
  "goal": "one sentence: what the user is trying to finish",
  "instructions": ["every rule/constraint/preference the assistant must keep following"],
  "decisions": [{"what": "...", "why": "... or null"}],
  "context": ["stable facts about the user or project needed to continue"],
  "open_tasks": ["what is still left to do"],
  "dead_ends": ["approaches tried that failed, so they are not repeated"],
  "artifact_ids": ["ids like m7b0 of the LATEST version of each thing being built"]
}"""

SYSTEM = (
    "You extract the WORKING STATE of a conversation between a user and an AI "
    "assistant, so a different AI can continue the work without the transcript. "
    "This is not a summary. Rules:\n"
    "- instructions: copy every standing rule the user gave (format, language, tone, "
    "things to avoid, constraints). Keep the user's wording. Drop rules the user later revoked.\n"
    "- artifact_ids: only the most recent version of each artifact; older drafts are superseded. "
    "Use ids exactly as shown in [CODE id ...] markers; use [] if there is none. "
    "Never mention these ids in any other field; say 'the latest code' instead.\n"
    "- Do not invent anything not present in the conversation.\n"
    f"Return ONLY a JSON object with this shape:\n{SCHEMA_HINT}"
)


def _build_messages(window: list[str], previous: dict | None) -> list[dict]:
    transcript = "\n\n".join(window)
    if previous is None:
        user = f"CONVERSATION:\n{transcript}"
    else:
        user = (
            "Here is the working state extracted from EARLIER parts of the conversation:\n"
            f"{json.dumps(previous, ensure_ascii=False)}\n\n"
            "Update it using the NEXT part of the conversation below. Keep everything still "
            "valid, add new items, replace the goal/artifacts if they changed, and remove "
            f"anything the user revoked.\n\nNEXT PART:\n{transcript}"
        )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _scrub_ids(value):
    """Internal block ids mean nothing to the next model; replace any that leak into text."""
    if isinstance(value, str):
        return BLOCK_ID.sub("the latest code", value)
    if isinstance(value, list):
        return [_scrub_ids(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_ids(v) for k, v in value.items()}
    return value


def _validate(raw: dict, blocks: dict[str, CodeBlock]) -> tuple[StateCard, dict]:
    ids = raw.get("artifact_ids") or []
    if not isinstance(ids, list):
        raise ValueError("artifact_ids must be a list")
    unknown = [i for i in ids if i not in blocks]
    if unknown:
        raise ValueError(f"unknown artifact ids {unknown}; valid ids: {sorted(blocks)}")
    fields = {k: _scrub_ids(v) for k, v in raw.items() if k != "artifact_ids"}
    fields["artifacts"] = [
        Artifact(block_id=b.block_id, language=b.language, content=b.content)
        for b in (blocks[i] for i in dict.fromkeys(ids))      # dedupe, keep order
    ]
    card = StateCard.model_validate(fields)
    # keep the id-form for the next rolling window (no code bodies resent)
    carry = card.model_dump(exclude={"artifacts"})
    carry["artifact_ids"] = [a.block_id for a in card.artifacts]
    return card, carry


def _extract_window(llm: LLM, window: list[str], previous: dict | None,
                    blocks: dict[str, CodeBlock]) -> tuple[StateCard, dict]:
    messages = _build_messages(window, previous)
    last_error = None
    for _ in range(MAX_RETRIES + 1):
        text = llm.complete(messages, json_mode=True, temperature=0.1)
        try:
            return _validate(parse_json(text), blocks)
        except (ValueError, ValidationError) as e:
            last_error = str(e)
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That output was invalid: {last_error}\n"
                                            "Return the corrected JSON object only."},
            ]
    raise ValueError(f"extraction failed after retries: {last_error}")


def extract_state(llm: LLM, conv: Conversation,
                  window_tokens: int = 3000) -> tuple[StateCard, int]:
    """Return (state card, number of windows used)."""
    lines, blocks = index_code_blocks(conv)
    windows = split_windows(lines, window_tokens)
    card, carry = None, None
    for window in windows:
        card, carry = _extract_window(llm, window, carry, blocks)
    return card, len(windows)

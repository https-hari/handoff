import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import Store
from app.extractor import extract_state, index_code_blocks, split_windows
from app.llm import parse_json
from app.render import render_prompt
from app.schemas import Conversation, StateCard
from app.verify import verify_reply


class FakeLLM:
    """Returns scripted responses in order and records the prompts it got."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, json_mode=False, temperature=0.1):
        self.calls.append(messages)
        r = self.responses.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


CONV = Conversation(source="chatgpt", messages=[
    {"role": "user", "content": "Help me write a C++ LRU cache. Use C++17 only, no Boost."},
    {"role": "assistant", "content": "Draft:\n```cpp\nclass LRU { /* v1 */ };\n```"},
    {"role": "user", "content": "Use list + unordered_map instead."},
    {"role": "assistant", "content": "Updated:\n```cpp\n#include <list>\nclass LRU { /* v2 */ };\n```"},
])

GOOD = {
    "goal": "Write an O(1) LRU cache in C++",
    "instructions": ["Use C++17 only", "No Boost"],
    "decisions": [{"what": "list + unordered_map", "why": "O(1) get/put"}],
    "context": [], "open_tasks": ["add tests"], "dead_ends": [],
    "artifact_ids": ["m3b0"],
}


def test_code_blocks_become_stubs_and_are_kept_verbatim():
    lines, blocks = index_code_blocks(CONV)
    assert set(blocks) == {"m1b0", "m3b0"}
    assert "class LRU { /* v2 */ };" in blocks["m3b0"].content
    assert "[CODE m3b0 | cpp | 2 lines]" in lines[3]


def test_split_windows_keeps_messages_whole():
    lines = ["a" * 400, "b" * 400, "c" * 400]          # ~100 tokens each
    assert [len(w) for w in split_windows(lines, 250)] == [2, 1]


def test_extract_copies_latest_artifact_verbatim():
    card, windows = extract_state(FakeLLM(GOOD), CONV)
    assert windows == 1
    assert card.artifacts[0].block_id == "m3b0"
    assert card.artifacts[0].content == "#include <list>\nclass LRU { /* v2 */ };"


def test_extract_retries_on_invalid_output():
    bad = dict(GOOD, artifact_ids=["m9b9"])            # id that does not exist
    llm = FakeLLM("not json at all", bad, GOOD)
    card, _ = extract_state(llm, CONV)
    assert card.goal == GOOD["goal"]
    assert len(llm.calls) == 3
    assert "unknown artifact ids" in llm.calls[2][-1]["content"]


def test_extract_gives_up_after_max_retries():
    with pytest.raises(ValueError):
        extract_state(FakeLLM("x", "y", "z"), CONV)


def test_rolling_extraction_passes_previous_state():
    second = dict(GOOD, instructions=GOOD["instructions"] + ["Add comments"])
    llm = FakeLLM(GOOD, GOOD, GOOD, second)
    card, windows = extract_state(llm, CONV, window_tokens=26)   # one message per window
    assert windows == 4
    assert "Add comments" in card.instructions
    assert "working state extracted from EARLIER" in llm.calls[1][1]["content"]
    assert "class LRU" not in llm.calls[1][1]["content"].split("NEXT PART")[0]  # no code resent


def test_parse_json_handles_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope this helps') == {"a": 2}


def test_render_targets():
    card, _ = extract_state(FakeLLM(GOOD), CONV)
    md = render_prompt(card, "chatgpt")
    xml = render_prompt(card, "claude")
    assert "## Rules to keep following\n1. Use C++17 only" in md
    assert "<rules_to_keep_following>" in xml and "</handoff>" in xml
    assert "class LRU { /* v2 */ };" in md and "/* v1 */" not in md


def test_verify_reports_missing_rules():
    card = StateCard(goal="goal", instructions=["Use C++17 only", "No Boost"])
    res = verify_reply(FakeLLM({"goal_correct": True, "kept": [True, False]}), card, "reply")
    assert res.missing == ["No Boost"] and res.retention == 0.5


def test_verify_retries_on_wrong_length():
    card = StateCard(goal="goal", instructions=["a", "b"])
    llm = FakeLLM({"goal_correct": True, "kept": [True]}, {"goal_correct": True, "kept": [True, True]})
    assert verify_reply(llm, card, "r").retention == 1.0


def test_api_end_to_end():
    store = Store(":memory:")
    llm = FakeLLM(GOOD, {"goal_correct": True, "kept": [True, False]})
    main.app.dependency_overrides[main.get_llm] = lambda: llm
    main.app.dependency_overrides[main.get_store] = lambda: store
    client = TestClient(main.app)
    try:
        r = client.post("/handoff", json={"conversation": CONV.model_dump(), "target": "claude"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stats"]["windows"] == 1 and body["handoff_id"] == 1

        v = client.post("/verify", json={"handoff_id": 1, "card": body["card"], "reply": "..."})
        assert v.status_code == 200 and v.json()["missing"] == ["No Boost"]

        s = client.get("/stats").json()
        assert s["handoffs"] == 1 and s["instruction_retention"] == 0.5 and s["goal_accuracy"] == 1.0
    finally:
        main.app.dependency_overrides.clear()


def test_api_rejects_empty_conversation():
    main.app.dependency_overrides[main.get_llm] = lambda: FakeLLM()
    try:
        r = TestClient(main.app).post("/handoff", json={"conversation": {"source": "x", "messages": []}})
        assert r.status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_block_ids_never_leak_into_text():
    leaky = dict(GOOD, open_tasks=["Finish get and put in m3b0", "Test [CODE m1b0] too",
                                   "Implemented get in version m3b0", "Fix the code m1b0"])
    card, _ = extract_state(FakeLLM(leaky), CONV)
    assert card.open_tasks == ["Finish get and put in the latest code", "Test the latest code too",
                               "Implemented get in the latest code", "Fix the latest code"]
    assert card.artifacts[0].block_id == "m3b0"


def test_one_huge_message_is_split_into_windows():
    lines = ["\n".join(["x" * 200] * 100)]              # ~5000 tokens, one message
    wins = split_windows(lines, 1000)
    assert len(wins) >= 5
    assert all(len(p) <= 4000 for w in wins for p in w)


def test_json_mode_rejection_falls_back_to_plain_mode(monkeypatch):
    import httpx
    from app import llm as L
    seen = []

    def fake_call(self, p, messages, json_mode, temperature):
        seen.append(json_mode)
        if json_mode:
            req = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("bad", request=req, response=httpx.Response(
                400, request=req, text='{"error":{"code":"json_validate_failed"}}'))
        return '{"ok": true}'

    monkeypatch.setattr(L.FallbackLLM, "_call", fake_call)
    out = L.FallbackLLM([L.Provider("groq", "u", "k", "m")]).complete([], json_mode=True)
    assert out == '{"ok": true}' and seen == [True, False]


def test_verify_evidence_format_and_no_quote_means_missing():
    card = StateCard(goal="goal", instructions=["Use C++17 only", "No Boost", "snake_case"])
    out = {"goal_correct": True, "rules": [
        {"n": 2, "evidence": "no Boost", "kept": True},
        {"n": 1, "evidence": "C++17 only", "kept": True},
        {"n": 3, "evidence": "", "kept": True}]}          # claims kept but quotes nothing
    res = verify_reply(FakeLLM(out), card, "reply")
    assert [c.kept for c in res.checks] == [True, True, False] and res.missing == ["snake_case"]

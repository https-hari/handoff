import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))

import handoff as cli  # noqa: E402

from app.render import render_prompt  # noqa: E402
from app.schemas import StateCard, Workspace  # noqa: E402


# ---------------------------------------------------------------- parsing
def test_parse_inline_labels():
    turns = cli.parse_chat("User: write code\nin C++\nAssistant: ok\n```cpp\nx\n```\nYou: thanks")
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"] == "write code\nin C++"
    assert "```cpp\nx\n```" in turns[1]["content"]


def test_parse_label_on_own_line_like_chatgpt_copy():
    text = "You said:\nUse C++17 only\nChatGPT said:\nSure, here it is.\nYou said:\nthanks"
    turns = cli.parse_chat(text)
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"] == "Use C++17 only"


def test_parse_without_labels_falls_back_to_one_block():
    turns = cli.parse_chat("just some text\nwith no speakers")
    assert len(turns) == 1 and "speaker labels were not preserved" in turns[0]["content"]


def test_parse_extension_export_json():
    data = {"source": "chatgpt", "messages": [{"role": "user", "content": "a"},
                                              {"role": "assistant", "content": "b"}]}
    assert cli.parse_chat(json.dumps(data)) == data["messages"]


# ----------------------------------------------------------------- repo
@pytest.fixture
def repo(tmp_path):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp_path / "cache.cpp").write_text("int get();\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "add cache skeleton")
    (tmp_path / "cache.cpp").write_text("int get();\nvoid put();\n")
    (tmp_path / "new.txt").write_text("hi\n")
    return tmp_path


def test_collect_workspace(repo):
    ws = cli.collect_workspace(repo, test_cmd="echo 3 passed")
    assert ws["branch"] == "main"
    assert ws["recent_commits"][0].endswith("add cache skeleton")
    assert any("cache.cpp" in f for f in ws["changed_files"])
    assert any("new.txt" in f for f in ws["changed_files"])
    assert "+void put();" in ws["diff"] and ws["diff_truncated"] is False
    assert ws["test_exit_code"] == 0 and "3 passed" in ws["test_output"]


def test_collect_workspace_rejects_non_repo(tmp_path):
    with pytest.raises(RuntimeError):
        cli.collect_workspace(tmp_path)


def test_workspace_rendered_verbatim(repo):
    ws = Workspace(**cli.collect_workspace(repo, test_cmd="exit 1"))
    prompt = render_prompt(StateCard(goal="Finish the cache"), "chatgpt", ws)
    assert "## Workspace state (from git, exact)" in prompt
    assert "Branch: main" in prompt and "+void put();" in prompt
    assert "FAILED (exit 1)" in prompt


# ------------------------------------------------------------- commands
def test_build_and_check_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    clipboard = {"text": "User: use C++17\nAssistant: ok"}
    calls = []

    def fake_api(path, body=None):
        calls.append((path, body))
        if path == "/handoff":
            return {"handoff_id": 7, "prompt": "PROMPT",
                    "card": {"goal": "g", "instructions": ["use C++17", "no Boost"], "artifacts": []},
                    "stats": {"prompt_tokens": 50, "source_tokens": 500}}
        return {"goal_correct": True, "checks": [{}, {}], "missing": ["no Boost"]}

    read = lambda: clipboard["text"]
    def write(t): clipboard["text"] = t

    args = argparse.Namespace(file=None, to="claude", repo=None, test=None)
    assert cli.cmd_build(args, call=fake_api, read=read, write=write) == 0
    assert clipboard["text"] == "PROMPT"
    assert calls[0][1]["conversation"]["messages"][0] == {"role": "user", "content": "use C++17"}

    clipboard["text"] = "1. use C++17"                          # the new model's reply
    assert cli.cmd_check(argparse.Namespace(file=None), call=fake_api, read=read, write=write) == 1
    assert calls[1][1]["handoff_id"] == 7
    assert "1. no Boost" in clipboard["text"]
    assert "1/2 rules kept" in capsys.readouterr().out


def test_check_without_build(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "none")
    with pytest.raises(SystemExit):
        cli.cmd_check(argparse.Namespace(file=None), read=lambda: "x")


def test_test_flag_requires_repo():
    with pytest.raises(SystemExit):
        cli.main(["build", "--test", "pytest"])


def test_check_refuses_when_clipboard_still_has_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    cli._save_last({"handoff_id": 1, "card": {"instructions": []}, "prompt": "PROMPT"})
    with pytest.raises(SystemExit, match="still holds the handoff"):
        cli.cmd_check(argparse.Namespace(file=None), call=lambda *a: {}, read=lambda: "PROMPT\n")

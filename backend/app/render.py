"""Render a StateCard into the message the user pastes into the new chat."""
from typing import Optional

from .schemas import StateCard, Target, Workspace

INTRO = ("I'm continuing work I started in another AI chat. Below is the working "
         "state of that conversation. Treat the rules as binding for the rest of this chat.")

CHECK = ("Before we continue, reply with ONLY: (1) the goal in one line, and "
         "(2) the numbered list of rules you will follow. Then wait for my next message.")


def _bullets(items, numbered=False):
    return "\n".join(f"{i}. {x}" if numbered else f"- {x}"
                     for i, x in enumerate(items, 1))


def _sections(card: StateCard) -> list[tuple[str, str]]:
    out = [("Goal", card.goal)]
    if card.instructions:
        out.append(("Rules to keep following", _bullets(card.instructions, numbered=True)))
    if card.context:
        out.append(("Context", _bullets(card.context)))
    if card.decisions:
        out.append(("Decisions already made", _bullets(
            f"{d.what}" + (f" (because {d.why})" if d.why else "") for d in card.decisions)))
    if card.artifacts:
        body = "\n\n".join(f"```{a.language or ''}\n{a.content}\n```" for a in card.artifacts)
        out.append(("Latest version of the work (verbatim)", body))
    if card.open_tasks:
        out.append(("Still to do", _bullets(card.open_tasks)))
    if card.dead_ends:
        out.append(("Already tried, did not work (don't repeat)", _bullets(card.dead_ends)))
    return out


def _workspace_section(ws: Workspace) -> tuple[str, str]:
    parts = []
    if ws.branch:
        parts.append(f"Branch: {ws.branch}")
    if ws.recent_commits:
        parts.append("Recent commits:\n" + _bullets(ws.recent_commits))
    if ws.changed_files:
        parts.append("Uncommitted changes:\n" + _bullets(ws.changed_files))
    if ws.test_command:
        status = "passed" if ws.test_exit_code == 0 else f"FAILED (exit {ws.test_exit_code})"
        out = f"\n```\n{ws.test_output.strip()}\n```" if ws.test_output else ""
        parts.append(f"Tests (`{ws.test_command}`): {status}{out}")
    if ws.diff:
        note = " (truncated)" if ws.diff_truncated else ""
        parts.append(f"Current diff{note}:\n```diff\n{ws.diff.rstrip()}\n```")
    return "Workspace state (from git, exact)", "\n\n".join(parts) or "No changes."


def render_prompt(card: StateCard, target: Target = "generic",
                  workspace: Optional[Workspace] = None) -> str:
    sections = _sections(card)
    if workspace is not None:
        sections.append(_workspace_section(workspace))
    if target == "claude":
        # Claude follows XML-tagged sections well
        tag = lambda h: h.lower().split(" (")[0].replace(" ", "_").replace(",", "")
        body = "\n".join(f"<{tag(h)}>\n{b}\n</{tag(h)}>" for h, b in sections)
        return f"{INTRO}\n\n<handoff>\n{body}\n</handoff>\n\n{CHECK}"
    body = "\n\n".join(f"## {h}\n{b}" for h, b in sections)
    return f"{INTRO}\n\n{body}\n\n{CHECK}"

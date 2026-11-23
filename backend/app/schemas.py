"""Data shapes shared by the API, the extractor and the eval harness."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["user", "assistant"]
Target = Literal["chatgpt", "claude", "gemini", "generic"]


class Message(BaseModel):
    role: Role
    content: str


class Conversation(BaseModel):
    source: str = "unknown"          # chatgpt / claude / gemini / paste
    messages: list[Message] = Field(min_length=1)


class Decision(BaseModel):
    what: str
    why: Optional[str] = None


class Artifact(BaseModel):
    """The latest version of whatever is being built (code, doc...).

    Content is copied verbatim from the source conversation, never
    rewritten by the model, so it cannot be hallucinated.
    """
    block_id: str
    language: Optional[str] = None
    content: str


class StateCard(BaseModel):
    """The working state of a conversation, not a summary of it."""
    goal: str = Field(min_length=3)
    instructions: list[str] = Field(default_factory=list)   # rules to keep following
    decisions: list[Decision] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)        # facts about the user / project
    open_tasks: list[str] = Field(default_factory=list)
    dead_ends: list[str] = Field(default_factory=list)      # tried and failed
    artifacts: list[Artifact] = Field(default_factory=list)


class Workspace(BaseModel):
    """Repo state collected by the CLI for coding work. Rendered verbatim,
    never passed through the LLM, so file names and diffs stay exact."""
    branch: Optional[str] = None
    recent_commits: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    diff: Optional[str] = None
    diff_truncated: bool = False
    test_command: Optional[str] = None
    test_exit_code: Optional[int] = None
    test_output: Optional[str] = None


class HandoffRequest(BaseModel):
    conversation: Conversation
    target: Target = "generic"
    workspace: Optional[Workspace] = None


class HandoffStats(BaseModel):
    source_tokens: int
    prompt_tokens: int
    compression: float               # prompt_tokens / source_tokens
    windows: int                     # extraction passes used


class HandoffResponse(BaseModel):
    handoff_id: int
    card: StateCard
    prompt: str
    stats: HandoffStats


class VerifyRequest(BaseModel):
    handoff_id: Optional[int] = None
    card: StateCard
    reply: str = Field(min_length=1)


class InstructionCheck(BaseModel):
    instruction: str
    kept: bool


class VerifyResponse(BaseModel):
    goal_correct: bool
    checks: list[InstructionCheck]
    missing: list[str]
    retention: float                 # kept / total (1.0 when no instructions)

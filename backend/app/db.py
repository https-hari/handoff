"""SQLite log of handoffs and verification results.

Two tables:
  handoffs(id, created_at, source, target, source_tokens, prompt_tokens, windows, card_json)
  verifications(id, handoff_id -> handoffs.id, created_at, goal_correct, kept, total)

One handoff can be verified several times (e.g. after re-sending missing
rules), so verifications is a separate 1:N table instead of columns on
handoffs. Stats are computed from the latest verification per handoff.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    source         TEXT    NOT NULL,
    target         TEXT    NOT NULL,
    source_tokens  INTEGER NOT NULL CHECK (source_tokens > 0),
    prompt_tokens  INTEGER NOT NULL CHECK (prompt_tokens > 0),
    windows        INTEGER NOT NULL CHECK (windows >= 1),
    card_json      TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS verifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id    INTEGER NOT NULL REFERENCES handoffs(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    goal_correct  INTEGER NOT NULL CHECK (goal_correct IN (0, 1)),
    kept          INTEGER NOT NULL CHECK (kept >= 0),
    total         INTEGER NOT NULL CHECK (total >= kept)
);
CREATE INDEX IF NOT EXISTS idx_verif_handoff ON verifications(handoff_id);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = self._mem or sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            if self._mem is None:
                c.close()

    def add_handoff(self, source, target, source_tokens, prompt_tokens, windows, card_json) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO handoffs (source, target, source_tokens, prompt_tokens, windows, card_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (source, target, source_tokens, prompt_tokens, windows, card_json))
            return cur.lastrowid

    def add_verification(self, handoff_id, goal_correct, kept, total) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO verifications (handoff_id, goal_correct, kept, total)"
                      " VALUES (?, ?, ?, ?)", (handoff_id, int(goal_correct), kept, total))

    def stats(self) -> dict:
        with self.conn() as c:
            n, avg_comp = c.execute(
                "SELECT COUNT(*), AVG(1.0 * prompt_tokens / source_tokens) FROM handoffs").fetchone()
            row = c.execute("""
                WITH latest AS (
                    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY handoff_id ORDER BY id DESC) AS rn
                    FROM verifications v
                )
                SELECT COUNT(*), SUM(kept), SUM(total), AVG(goal_correct)
                FROM latest WHERE rn = 1
            """).fetchone()
        verified, kept, total, goal = row
        return {
            "handoffs": n,
            "avg_compression": round(avg_comp, 3) if avg_comp is not None else None,
            "verified_handoffs": verified,
            "instruction_retention": round(kept / total, 3) if total else None,
            "goal_accuracy": round(goal, 3) if goal is not None else None,
        }

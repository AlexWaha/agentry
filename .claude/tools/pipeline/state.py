#!/usr/bin/env python3
"""Pipeline run state - the orchestrator's memory outside the model.

A tiny SQLite store (stdlib only) holding one row per task that is currently
moving through the execution pipeline, plus an append-only gate log. Hook scripts
(advance.py, gate.py, stop_gate.py, pretool_gate.py, approve.py) read and write
through this module so the FSM decision is made by deterministic code, never by
the probabilistic model.

This is the local stand-in for the Hydra OS orchestrator state: the model
proposes a transition, but only these scripts can record one.

CLI:
    python state.py --show        # dump all runs as JSON (debugging)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / ".claude" / "state"
DB_PATH = STATE_DIR / "run.db"
PIPELINE_PATH = ROOT / ".claude" / "pipeline.json"
BACKLOG_DIR = ROOT / ".claude" / "tasks" / "backlog"
ACTIVE_DIR = ROOT / ".claude" / "tasks" / "active"
DONE_DIR = ROOT / ".claude" / "tasks" / "done"

# The folder a task file sits in IS its state. There is no `status:` field to
# drift out of sync with it: backlog = queued, active = moving through a
# pipeline, done = merged into the main branch and deployed.
TASK_DIRS = {"backlog": BACKLOG_DIR, "active": ACTIVE_DIR, "done": DONE_DIR}

BUILD = "build"
PLAN = "plan"

# Stage status values
ST_IN_PROGRESS = "in_progress"
ST_GATE_PASSED = "gate_passed"
ST_GATE_FAILED = "gate_failed"
ST_BLOCKED = "blocked"

EDITING_STAGES = ("implement", "test", "review")


def now() -> str:
    """Machine timestamp for the run log: UTC, explicitly marked Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    """Calendar date for text a HUMAN reads (task files, handoff docs, review
    stamps) - the machine's LOCAL date, so `completed: 2026-09-13` matches the
    CEO's own clock. A bare UTC date silently shows tomorrow (or yesterday) for
    anyone far enough from UTC, which is how a handoff doc ends up dated a day
    off its own commit. Machine timestamps stay UTC - see now().
    `.astimezone()` converts the UTC-aware value to local, which keeps the call
    timezone-aware (ruff DTZ) instead of using a naive date.today()."""
    return datetime.now(timezone.utc).astimezone().date().isoformat()


# --- pipeline.json (declarative stage config) -------------------------------

def load_pipeline() -> dict:
    """Load the declarative pipeline config. Returns {} if missing/unparseable
    so callers can fail open (a broken config must never brick the agent)."""
    try:
        return json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stages(pipeline: dict, which: str = BUILD) -> list[dict]:
    """Stages of one pipeline. Falls back to the top-level `stages` array so a
    config written before pipelines were plural still drives the build flow."""
    block = (pipeline.get("pipelines") or {}).get(which) or {}
    raw = block.get("stages")
    if raw is None and which == BUILD:
        raw = pipeline.get("stages", [])
    return [s for s in (raw or []) if isinstance(s, dict)]


def stage_names(pipeline: dict, which: str = BUILD) -> list[str]:
    return [s.get("name", "") for s in stages(pipeline, which)]


def get_stage(pipeline: dict, name: str, which: str = BUILD) -> dict | None:
    for s in stages(pipeline, which):
        if s.get("name") == name:
            return s
    return None


def next_stage(pipeline: dict, name: str, which: str = BUILD) -> str | None:
    names = stage_names(pipeline, which)
    if name in names:
        idx = names.index(name)
        if idx + 1 < len(names):
            return names[idx + 1]
    return None


def first_stage(pipeline: dict, which: str = BUILD) -> str:
    names = stage_names(pipeline, which)
    return names[0] if names else "implement"


def editing_stages(pipeline: dict, which: str = BUILD) -> tuple[str, ...]:
    """Stages during which the working tree is being changed. Only these are
    serialized across tasks; a planning stage writes documents, not code."""
    block = (pipeline.get("pipelines") or {}).get(which) or {}
    raw = block.get("editing_stages")
    if raw is None:
        return EDITING_STAGES if which == BUILD else ()
    return tuple(str(s) for s in raw)


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 3000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create schema idempotently. Safe to call on every connect."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            task            TEXT PRIMARY KEY,
            type            TEXT DEFAULT 'feature',
            stage           TEXT,
            stage_status    TEXT,
            awaiting_human  TEXT DEFAULT '',
            commit_approved INTEGER DEFAULT 0,
            push_approved   INTEGER DEFAULT 0,
            retries         INTEGER DEFAULT 0,
            continuations   INTEGER DEFAULT 0,
            updated         TEXT
        );
        CREATE TABLE IF NOT EXISTS gate_log (
            task TEXT,
            stage TEXT,
            cmd TEXT,
            exit INTEGER,
            at TEXT
        );
        """
    )
    # Added after the first runs existed: default keeps them on the build flow.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    if "pipeline" not in cols:
        conn.execute(f"ALTER TABLE runs ADD COLUMN pipeline TEXT DEFAULT '{BUILD}'")
    conn.commit()


def get_run(conn: sqlite3.Connection, task: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE task = ?", (task,)).fetchone()
    return dict(row) if row else None


def all_runs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY task").fetchall()
    return [dict(r) for r in rows]


def create_run(conn: sqlite3.Connection, task: str, task_type: str, stage: str,
               which: str = BUILD) -> dict:
    conn.execute(
        "INSERT INTO runs (task, type, stage, stage_status, awaiting_human, pipeline, updated) "
        "VALUES (?, ?, ?, ?, '', ?, ?)",
        (task, task_type, stage, ST_IN_PROGRESS, which, now()),
    )
    conn.commit()
    return get_run(conn, task)  # type: ignore[return-value]


def run_pipeline(run: dict | None) -> str:
    return (run or {}).get("pipeline") or BUILD


def set_fields(conn: sqlite3.Connection, task: str, **fields) -> None:
    if not fields:
        return
    fields["updated"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE runs SET {cols} WHERE task = ?", (*fields.values(), task))
    conn.commit()


def task_dir(task: str) -> str | None:
    """Which of the three folders currently holds the task file."""
    for name, folder in TASK_DIRS.items():
        if (folder / f"{task}.md").is_file():
            return name
    return None


def move_task(task: str, to: str) -> bool:
    """Move the task file into one of backlog/active/done.

    The folder is the state, so this single move IS the status change - there is
    no frontmatter field to keep in step with it. Only `done` stamps a date,
    because that is the one transition worth reading off the file itself.

    Fail-open: any error returns False without raising, so bookkeeping can never
    brick the pipeline."""
    dst_dir = TASK_DIRS.get(to)
    if dst_dir is None:
        return False
    try:
        src_name = task_dir(task)
        if src_name is None or src_name == to:
            return False
        src = TASK_DIRS[src_name] / f"{task}.md"
        text = src.read_text(encoding="utf-8", errors="replace")
        if to == "done":
            stamp = today()  # local date - the CEO reads this in the task file
            if re.search(r"(?m)^completed:", text):
                text = re.sub(r"(?m)^completed:.*$", f"completed: {stamp}", text, count=1)
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{task}.md").write_text(text, encoding="utf-8")
        src.unlink()
        return True
    except Exception:
        return False


def move_task_to_done(task: str) -> bool:
    return move_task(task, "done")


def log_gate(conn: sqlite3.Connection, task: str, stage: str, cmd: str, exit_code: int) -> None:
    conn.execute(
        "INSERT INTO gate_log (task, stage, cmd, exit, at) VALUES (?, ?, ?, ?, ?)",
        (task, stage, cmd, exit_code, now()),
    )
    conn.commit()


def _resume_text(conn: sqlite3.Connection) -> str:
    """One-line-per-run summary for SessionStart context injection."""
    live = [r for r in all_runs(conn) if r["stage"] != "done"]
    if not live:
        return ""
    lines = ["Pipeline state (resume in-flight work, do not restart from scratch):"]
    for r in live:
        tail = f" awaiting_human={r['awaiting_human']}" if r["awaiting_human"] else ""
        lines.append(f"  - {r['task']}: stage={r['stage']} status={r['stage_status']}{tail}")
    lines.append("Drive these through the pipeline via tools/pipeline/advance.py without asking the user.")
    return "\n".join(lines)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline run state inspector")
    parser.add_argument("--show", action="store_true", help="dump all runs as JSON")
    parser.add_argument("--resume", action="store_true", help="print resume summary for SessionStart")
    args = parser.parse_args()
    conn = connect()
    if args.show:
        print(json.dumps(all_runs(conn), indent=2))
    if args.resume:
        text = _resume_text(conn)
        if text:
            print(text)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

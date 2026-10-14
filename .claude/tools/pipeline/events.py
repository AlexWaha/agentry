#!/usr/bin/env python3
"""Append-only event journal for the pipeline (task-0046).

run.db holds CURRENT state and overwrites history. This module is the history:
one row per transition, gate run, rejection, approval, park and block, in a
separate SQLite file (`.agentry/state/events.db`, one per workspace, `lane` is a
column). It answers "how many rejections did this task cost", "how long did each
stage attempt take" and "how often does a gate fail" - numbers that were
otherwise counted by hand and lost.

Properties, all deliberate:
  * Append-only by construction: this module has no update or delete function,
    and BEFORE UPDATE / BEFORE DELETE triggers abort any such statement.
  * Fail-open: append() never raises and never blocks (2 s lock timeout). A
    skipped event prints `[event-skipped] ...` to stderr and the caller carries
    on. Measurement must not become a gate: no gate reads this journal. Its only
    readers are the merged dedupe in state.py and supervisor.progress_at(), which
    uses a recent `gate` row as evidence of progress - that can only turn
    STALLED into HEALTHY (a veto on a relaunch), never cause an action.
  * Stdlib only, and it does NOT import state.py - state.py will import this
    module to write its rows, so the dependency runs one way.

Row vocabulary. `kind` is a closed set (KINDS). Conventions the writers follow:
  registered   stage = first stage of the run
  transition   stage = the stage entered, from_stage = the stage left
  gate         stage = gate stage, outcome = "pass" | "fail",
               detail = {cmd, exit, duration_ms}; gate OUTPUT is never stored
  rejected     stage = stage the task was sent back to, detail = {reason}
  approved     outcome = checkpoint name (plan, commit, push, trunk_push)
  blocked / parked / continued / paused / resumed / merged / closed
               stage = stage at the time, detail = {reason} where one exists
  abandoned    stage = done, from_stage = the stage left, detail = {reason}
`via` is the stem of the writing script (`approve` = the User, `advance` = a
level grant). `at` is UTC, second resolution, written by this module.

CLI:
    python events.py --task task-0042          # per-task report (JSON)
    python events.py --summary [--since DATE]  # cross-task aggregates (JSON)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / ".agentry" / "state"
DB_NAME = "events.db"

# The writer must never wait long: a held lock skips the event, it does not stall
# a transition.
TIMEOUT_SECONDS = 2

KINDS = frozenset({
    "registered", "transition", "gate", "blocked", "parked", "approved",
    "rejected", "continued", "merged", "closed", "paused", "resumed",
    "abandoned",
})

GATE_PASS = "pass"
GATE_FAIL = "fail"

# A detail larger than this is replaced by a marker: the journal records facts,
# not payloads.
MAX_DETAIL_CHARS = 4096

# Same shape state.py enforces for PIPELINE_LANE; re-checked here because this
# module may not import state.
_LANE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?$")

_SCHEMA = (
    """CREATE TABLE events (
        id         INTEGER PRIMARY KEY,
        at         TEXT NOT NULL DEFAULT '',
        lane       TEXT NOT NULL DEFAULT '',
        task       TEXT NOT NULL DEFAULT '',
        pipeline   TEXT NOT NULL DEFAULT '',
        kind       TEXT NOT NULL DEFAULT '',
        stage      TEXT NOT NULL DEFAULT '',
        from_stage TEXT NOT NULL DEFAULT '',
        outcome    TEXT NOT NULL DEFAULT '',
        via        TEXT NOT NULL DEFAULT '',
        detail     TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX events_task_id ON events (task, id)",
    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
       BEGIN SELECT RAISE(ABORT, 'events is append-only'); END""",
    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
       BEGIN SELECT RAISE(ABORT, 'events is append-only'); END""",
)

_INSERT = (
    "INSERT INTO events (at, lane, task, pipeline, kind, stage, from_stage, "
    "outcome, via, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def db_path() -> Path:
    return STATE_DIR / DB_NAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lane() -> str:
    raw = os.environ.get("PIPELINE_LANE", "").strip()
    return raw if _LANE_RE.match(raw) else ""


def _skip(why: str) -> None:
    try:
        sys.stderr.write(f"[event-skipped] {why}\n")
    except Exception:
        pass


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    return row is not None


def _copy_gate_log(conn: sqlite3.Connection, state_dir: Path) -> None:
    """Copy the legacy gate_log rows of every run*.db into the new journal.

    Read-only on the sources: the live gate_log table is never dropped or
    modified here. A source that cannot be read is reported and skipped.
    """
    for source in sorted(state_dir.glob("run*.db")):
        lane = source.stem[len("run"):].lstrip(".")
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True,
                                         timeout=TIMEOUT_SECONDS)) as old:
                if old.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                               "AND name='gate_log'").fetchone() is None:
                    continue  # fresh workspace: init_db no longer creates gate_log
                rows = old.execute(
                    "SELECT task, stage, cmd, exit, at FROM gate_log ORDER BY rowid"
                ).fetchall()
        except sqlite3.Error as exc:
            _skip(f"gate_log copy from {source.name}: {exc}")
            continue
        conn.executemany(_INSERT, [
            (at or "", lane, task or "", "", "gate", stage or "", "",
             GATE_PASS if code == 0 else GATE_FAIL, "migrated",
             json.dumps({"cmd": cmd or "", "exit": code}, sort_keys=True))
            for task, stage, cmd, code, at in rows
        ])


def _init(conn: sqlite3.Connection, state_dir: Path) -> None:
    """Create the schema once. BEGIN IMMEDIATE serialises concurrent first
    writers: the loser re-checks and finds the table already there."""
    # WAL (persistent, set once) and synchronous=NORMAL drop the per-commit
    # fsync: measured, four processes appending 50 events each starved one writer
    # past the 2 s bound in rollback-journal mode. The journal is evidence, so
    # losing the last commit on power failure is acceptable.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _has_table(conn):
            for statement in _SCHEMA:
                conn.execute(statement)
            _copy_gate_log(conn, state_dir)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=TIMEOUT_SECONDS, isolation_level=None)
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
        if not _has_table(conn):
            _init(conn, path.parent)
    except BaseException:
        conn.close()
        raise
    return conn


def _open_existing() -> sqlite3.Connection | None:
    """Read-side connection: never creates the file."""
    path = db_path()
    if not path.is_file():
        return None
    conn = sqlite3.connect(path, timeout=TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        found = _has_table(conn)
    except BaseException:
        conn.close()
        raise
    if not found:
        conn.close()
        return None
    return conn


def append(kind: str, task: str, *, stage: str = "", from_stage: str = "",
           outcome: str = "", via: str = "", detail: dict | None = None,
           pipeline: str = "") -> bool:
    """Append one event. Never raises; returns False (after a stderr note) when
    the event was skipped."""
    try:
        if kind not in KINDS:
            _skip(f"unknown kind {kind!r}")
            return False
        body = json.dumps(detail or {}, sort_keys=True, ensure_ascii=True)
        if len(body) > MAX_DETAIL_CHARS:
            body = json.dumps({"truncated": len(body)})
        with closing(_connect()) as conn:
            conn.execute(_INSERT, (
                _now(), _lane(), task or "", pipeline, kind, stage, from_stage,
                outcome, via, body))
        return True
    except Exception as exc:
        _skip(f"{kind} {task}: {exc}")
        return False


def last_at(task: str, kind: str = "") -> str:
    """Timestamp of the newest event for `task` (optionally of one kind), or ""
    when there is none or the journal cannot be read."""
    try:
        conn = _open_existing()
        if conn is None:
            return ""
        with closing(conn):
            sql = "SELECT at FROM events WHERE task = ?"
            args: list = [task]
            if kind:
                sql += " AND kind = ?"
                args.append(kind)
            row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
            return row["at"] if row else ""
    except Exception:
        return ""


def _ts(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _seconds(start: str, end: str) -> int | None:
    a, b = _ts(start), _ts(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds())


def _detail(row: sqlite3.Row) -> dict:
    try:
        value = json.loads(row["detail"])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def task_report(task: str) -> dict:
    """Everything the journal knows about one task: how often each stage was
    entered, how long each attempt took, every rejection with its reason, and the
    gate record per stage."""
    report: dict = {"task": task, "events": 0, "stages": {}, "rejections": [],
                    "gates": {}, "merged": False}
    conn = _open_existing()
    if conn is None:
        return report
    with closing(conn):
        rows = conn.execute(
            "SELECT * FROM events WHERE task = ? ORDER BY id", (task,)).fetchall()
    report["events"] = len(rows)
    if not rows:
        return report

    entries = [r for r in rows if r["kind"] in ("registered", "transition")]
    last_event = rows[-1]["at"]
    ended = any(r["kind"] in ("merged", "closed", "abandoned") for r in rows)
    report["merged"] = any(r["kind"] == "merged" for r in rows)
    for index, entry in enumerate(entries):
        is_last = index + 1 == len(entries)
        end = last_event if is_last else entries[index + 1]["at"]
        stage = report["stages"].setdefault(
            entry["stage"], {"entries": 0, "attempts": []})
        stage["entries"] += 1
        stage["attempts"].append({
            "at": entry["at"],
            "seconds": _seconds(entry["at"], end),
            "open": is_last and not ended,
        })

    for row in rows:
        if row["kind"] == "rejected":
            report["rejections"].append({
                "at": row["at"], "stage": row["stage"],
                "from_stage": row["from_stage"],
                "reason": _detail(row).get("reason", row["outcome"])})
        elif row["kind"] == "gate":
            gate = report["gates"].setdefault(row["stage"], {"runs": 0, "failures": 0})
            gate["runs"] += 1
            gate["failures"] += row["outcome"] == GATE_FAIL
    return report


def summary(since: str = "") -> dict:
    """Cross-task aggregates: gate failure rate per stage, and registration-to-
    merge seconds per task. `since` (an ISO date or timestamp) restricts gate
    rows and merges to events at or after it."""
    result: dict = {"since": since, "gate_failure_rate": {},
                    "registration_to_merge": {}}
    conn = _open_existing()
    if conn is None:
        return result
    with closing(conn):
        gates = conn.execute(
            "SELECT stage, COUNT(*) AS runs, "
            "SUM(CASE WHEN outcome = ? THEN 1 ELSE 0 END) AS failures "
            "FROM events WHERE kind = 'gate' AND at >= ? GROUP BY stage",
            (GATE_FAIL, since)).fetchall()
        merges = conn.execute(
            "SELECT m.task AS task, "
            "(SELECT MIN(r.at) FROM events r WHERE r.task = m.task "
            "   AND r.kind = 'registered') AS registered, "
            "MAX(m.at) AS merged FROM events m "
            "WHERE m.kind = 'merged' AND m.at >= ? GROUP BY m.task",
            (since,)).fetchall()
    for row in gates:
        runs, failures = row["runs"], row["failures"] or 0
        result["gate_failure_rate"][row["stage"]] = {
            "runs": runs, "failures": failures,
            "rate": round(failures / runs, 4) if runs else 0.0}
    for row in merges:
        seconds = _seconds(row["registered"] or "", row["merged"])
        if seconds is not None:
            result["registration_to_merge"][row["task"]] = seconds
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the pipeline event journal.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", help="per-task report")
    group.add_argument("--summary", action="store_true", help="cross-task aggregates")
    parser.add_argument("--since", default="",
                        help="ISO date or timestamp (with --summary)")
    args = parser.parse_args(argv)
    if args.since and not _SINCE_RE.match(args.since):
        parser.error("--since must look like 2026-10-01 or 2026-10-01T09:30:00Z")
    try:
        data = task_report(args.task) if args.task else summary(args.since)
    except sqlite3.Error as exc:
        sys.stderr.write(f"events.py: cannot read {db_path()}: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

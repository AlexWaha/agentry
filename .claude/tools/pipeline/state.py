#!/usr/bin/env python3
"""Pipeline run state - the orchestrator's memory outside the model.

A tiny SQLite store (stdlib only) holding one row per task that is currently
moving through the execution pipeline. History does not live here: every write
below also appends to the event journal (events.py, task-0046), which is
evidence only and never read back to decide anything. Hook scripts
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
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# The journal is evidence, never a dependency: a missing or broken events.py must
# not take the run store (imported at the top of every hook) down with it.
try:
    import events
except Exception:
    events = None

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / ".agentry" / "state"

# A lane is one independent conveyor. PIPELINE_LANE=<name> in the environment of
# a session gives it its own run.db, mode and approvals file, so a session
# planning work does not share pipeline state with the session building a task.
# Unset is the default lane and is byte-identical to the behaviour before lanes
# existed - the suffix is empty, so every path resolves exactly as before.
#
# This is the ONE place the lane is read. mode.py, approvals.py and swarm.py
# suffix their own files from state.LANE_SUFFIX rather than re-reading the
# environment, for the same reason advance.py reads the workflow mode through a
# single accessor: four copies of one lookup are four chances to disagree about
# which lane the session is in.
# The lane name becomes part of a FILENAME, so it is validated rather than
# trusted. An unvalidated value was worse than sloppy: `a/b` raised a sqlite
# error that the hooks' top-level fail-open swallowed into exit 0, and
# `../../evil` opened (and created) a database outside the state directory
# entirely. Both spellings therefore turned the commit and push checkpoints off
# without a word.
LANE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LANE_ERROR = (
    "PIPELINE_LANE={value!r} is not a usable lane name. A lane becomes part of the "
    "run-store filename, so it must match {pattern} (letters, digits, underscore, "
    "hyphen; 32 characters at most). Falling back to the default lane - unset "
    "PIPELINE_LANE to silence this.")

# An unusable lane WARNS and falls back to the default lane; it must never raise
# here. This module is imported at the top of every hook, `SystemExit` is not
# caught by their `except Exception`, and exit 2 means opposite things per event:
# PreToolUse reads it as "deny this call" (so even Read and ls were denied) and
# Stop reads it as "you may not stop". One typo in a lane name therefore left the
# session with no tools and no way out, since the model cannot unset the parent
# process environment. That is exactly the bricking NFR-4 forbids.
#
# The checkpoint protection this used to raise for lives one layer down and is
# unaffected: a task registered in lane X has no row in the DEFAULT store, and
# the fail-closed "no row in the run store" path in pretool_gate.handle_bash()
# refuses commit and push, naming the lane in the message.
_RAW_LANE = os.environ.get("PIPELINE_LANE", "").strip()
if _RAW_LANE and not LANE_RE.match(_RAW_LANE):
    sys.stderr.write(LANE_ERROR.format(value=_RAW_LANE, pattern=LANE_RE.pattern) + "\n")
    _RAW_LANE = ""

LANE = _RAW_LANE
LANE_SUFFIX = f".{LANE}" if LANE else ""

DB_PATH = STATE_DIR / f"run{LANE_SUFFIX}.db"
PIPELINE_PATH = ROOT / ".agentry" / "pipeline.json"
BACKLOG_DIR = ROOT / ".agentry" / "tasks" / "backlog"
ACTIVE_DIR = ROOT / ".agentry" / "tasks" / "active"
DONE_DIR = ROOT / ".agentry" / "tasks" / "done"

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
# Deliberately set aside by the CEO's force-majeure call (task-0059): registered,
# not driven, not holding the working tree. `stage` is left where it was and the
# prior stage_status is kept in `paused_status`, so resume restores it exactly.
ST_PAUSED = "paused"
# Dropped on the CEO's decision (task-0122): `stage` is `done` although nothing
# merged, and `abandon_reason` carries why. Set only by approve.py --abandon.
ST_ABANDONED = "abandoned"

EDITING_STAGES = ("implement", "test", "review")

# The build flow's checkpoint stage, and the twin of EDITING_STAGES in every
# respect: a last-resort default for `build` only, used when pipeline.json
# cannot be read at all. Never consulted while the config parses.
CHECKPOINT_STAGES = ("ready",)

# How long a busy marker stays fresh with no explicit timeout of its own. The
# marker itself - its file, owner and lifecycle - lives in busy.py.
BUSY_TIMEOUT = 900


# --- debt-checker fail-open, out loud -----------------------------------------
# Every debt checker catches broadly and returns an empty result, because NFR-4
# says a bug in a checker must not brick the conveyor. That is right and is not
# in question. What was wrong is that it was SILENT: an empty list from a
# crashed checker is byte-identical to an empty list from a clean tree, so one
# typo turned enforcement into permission and the only symptom was that the
# pipeline stopped complaining - which reads exactly like success.
#
# Lives here because state.py is already imported at the top of every hook and
# nothing in this block can throw at import time (task-0077 Notes).
CHECKS_SKIPPED: list[str] = []
SKIPPED_PREFIX = "[debt-check-skipped]"


def checker_failed(name: str, exc: BaseException, fallback):
    """Record and announce a debt checker that raised, then fail open.

    Returns `fallback` so the call site's behaviour is unchanged - no site
    becomes fail-closed. The line goes to stderr on a fixed prefix so an
    automated reader can tell "skipped" from "clean" by grep rather than by
    reading prose, and the name is kept in CHECKS_SKIPPED so the block that
    consumes `fallback` can say the check was skipped instead of passing an
    uninformed release off as a verdict."""
    CHECKS_SKIPPED.append(name)
    print(f"{SKIPPED_PREFIX} {name}: {type(exc).__name__}: {exc}",
          file=sys.stderr, flush=True)
    return fallback


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
        data = json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


# --- lanes{} (contract C-4, FR-45) ---------------------------------------------

DEFAULT_LANE = "default"


def lane_name() -> str:
    """This session's lane: PIPELINE_LANE, or `default` when it is unset."""
    return LANE or DEFAULT_LANE


def lanes() -> dict[str, str]:
    """lane -> the one repo key it is bound to, from `lanes{}` in pipeline.json.

    `{}` means "no lane routing": the key is absent (a supported form, so silent)
    or it is not an object (one stderr line). Both fail open per NFR-4, and the
    caller treats them alike - offer the whole backlog. An entry without a
    non-empty string `repo` is dropped with one stderr line and the others
    survive, so one bad lane cannot switch the rest off. Keys starting with `_`
    are comments."""
    raw = load_pipeline().get("lanes")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        print("pipeline.json: lanes must be an object of lane -> {repo}; "
              "ignoring it and offering the whole backlog", file=sys.stderr)
        return {}
    out: dict[str, str] = {}
    for name, entry in raw.items():
        if str(name).startswith("_"):
            continue
        repo = entry.get("repo") if isinstance(entry, dict) else None
        if isinstance(repo, str) and repo.strip():
            out[str(name)] = repo.strip()
        else:
            print(f"pipeline.json: lane {name!r} needs a non-empty string "
                  f"\"repo\" (one repos{{}} key); lane dropped", file=sys.stderr)
    return out


def lane_repo(lane: str | None = None) -> str | None:
    """The repo key `lane` (default: this session's) is bound to, or None when
    lanes{} does not declare it."""
    return lanes().get(lane or lane_name())


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
    """Stages an agent owns and the conveyor drives to an exit gate. A run on one
    of them holds the working tree, so they are serialized across tasks.

    "Holds the tree", not "writes code": the plan flow's documents are files in
    this repository too, and reading the name literally is what left
    pipelines.plan.editing_stages empty and plan runs undriven until task-0020.
    Excluded are the stages an agent does NOT own - a checkpoint stage waiting on
    the CEO, and the terminal stage.

    An absent key falls back to the module constant for `build` and to nothing
    for any other flow (NFR-4): a flow whose stages we cannot read drives no run
    rather than driving every run."""
    block = (pipeline.get("pipelines") or {}).get(which) or {}
    raw = block.get("editing_stages")
    if raw is None:
        return EDITING_STAGES if which == BUILD else ()
    return tuple(str(s) for s in raw)


def checkpoint_stages(pipeline: dict, which: str = BUILD) -> tuple[str, ...]:
    """Stages whose definition declares a human checkpoint. The counterpart of
    editing_stages(): together they answer "who owns this stage", an agent or a
    person, and the Stop hook needs both to decide whether to drive a run and
    whether that run still holds the working tree.

    This exists so no caller has to spell a stage name. `stop_gate` compared
    `r["stage"] == "ready"` for exactly this, which was correct for `build` and
    blind everywhere else: the plan flow's `approval` is the same kind of stage
    and matched nothing, so the conveyor both nagged at it and read its tree as
    free. A second literal for `approval` would have been the same bug waiting
    on a third flow.

    A flow that declares no checkpoint stage gets an empty tuple - that is the
    config speaking, not a failure. Only an UNREADABLE flow (no stage names at
    all) falls back, and then to the module constant for `build` alone, exactly
    as editing_stages() does: a broken config must not make `ready` look like a
    free slot."""
    names = stage_names(pipeline, which)
    if not names:
        return CHECKPOINT_STAGES if which == BUILD else ()
    return tuple(
        str(s.get("name", "")) for s in stages(pipeline, which)
        if s.get("checkpoints")
    )


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 3000")
    try:
        init_db(conn)
    except Exception:
        conn.close()  # a handle left open keeps run.db locked on Windows
        raise
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
        """
    )
    _add_late_columns(conn)


def _late_columns() -> list[tuple[str, str]]:
    """The runs columns added after the first stores existed, with their DDL. A
    default keeps old rows on the build flow (`pipeline`), unapproved
    (`plan_approved`, the pause record) or empty (the frozen task-file fields)."""
    return [
        ("pipeline", f"TEXT DEFAULT '{BUILD}'"),
        # The plan flow's checkpoint, mirroring commit_approved: an old run is never
        # pre-approved.
        ("plan_approved", "INTEGER DEFAULT 0"),
        # Pause record (task-0059): the status the run had when it was paused, the
        # CEO's reason, and whether the tree was clean or already committed.
        ("paused_status", "TEXT DEFAULT ''"),
        ("pause_reason", "TEXT DEFAULT ''"),
        ("pause_kind", "TEXT DEFAULT ''"),
        # Why the CEO dropped the task (task-0122). Not a frozen field: it comes
        # from the abandon command, never from the task file.
        ("abandon_reason", "TEXT DEFAULT ''"),
        # Task-file decisions frozen at registration (task-0124): the closure sweep,
        # the umbrella debt cover, the commit-tag branch rule and the repo scoping
        # read these columns, never the file, which the agent being judged can
        # rewrite after the CEO's approval.
        *((col, "TEXT DEFAULT ''") for col in FROZEN_FIELDS),
    ]


def _add_late_columns(conn: sqlite3.Connection) -> None:
    """Add the missing late columns and freeze the new frozen ones from the task files.

    One `BEGIN IMMEDIATE` transaction holds the column check, the ALTERs and the
    backfill (SQLite DDL is transactional), so a failure part way leaves neither a
    column without its values nor a half-filled one: the next connect starts over.
    The column check is repeated under the lock, so a second process connecting
    for the first time waits for the first and then finds nothing to add.

    Only the columns added NOW are filled. Rows that predate them are frozen once,
    from the file as it reads at the upgrade (empty would be stricter, but it would
    strip the cover from every subtask of an umbrella that is already done and
    refuse every later registration); a column that already existed keeps what the
    registration recorded."""
    def missing() -> list[tuple[str, str]]:
        have = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        return [(col, ddl) for col, ddl in _late_columns() if col not in have]

    if not missing():
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        todo = missing()
        for col, ddl in todo:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {ddl}")
        fresh = [col for col, _ in todo if col in FROZEN_FIELDS]
        if fresh:
            for (task,) in conn.execute("SELECT task FROM runs").fetchall():
                values = {k: v for k, v in frozen_frontmatter(task).items() if k in fresh}
                conn.execute("UPDATE runs SET " + ", ".join(f"{k} = ?" for k in values)
                             + " WHERE task = ?", (*values.values(), task))
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        if missing():
            raise


def get_run(conn: sqlite3.Connection, task: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE task = ?", (task,)).fetchone()
    return dict(row) if row else None


def all_runs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY task").fetchall()
    return [dict(r) for r in rows]


# --- event journal (task-0046) ----------------------------------------------------
# Every run-store write below is mirrored into events.py AFTER its commit. The
# journal is append-only evidence: nothing here or anywhere reads it to decide a
# transition, and a failed append is reported on stderr and ignored.

def _via() -> str:
    """The stem of the script that made this write (`approve` = the CEO,
    `advance` = a level grant, `stop_gate`, `supervisor`). '' when argv[0] is not a
    plain name, so a hostile argv cannot put free text in the journal."""
    stem = Path(sys.argv[0] or "").stem if sys.argv else ""
    return stem if LANE_RE.match(stem) else ""


@contextmanager
def _beside_run_store():
    """Yield the events module pointed at the directory of the run store in use,
    or None when it did not import. The journal lives next to run.db, so a caller
    that moves DB_PATH (every test sandbox) moves the journal with it and nothing
    leaks into the live one; the module's own setting is put back afterwards."""
    if events is None:
        yield None
        return
    saved = events.STATE_DIR
    events.STATE_DIR = Path(DB_PATH).parent
    try:
        yield events
    finally:
        events.STATE_DIR = saved


def emit(kind: str, task: str, **fields) -> bool:
    """Append one journal row. Never raises; False when it was skipped."""
    try:
        with _beside_run_store() as journal:
            if journal is None:
                return False
            fields.setdefault("via", _via())
            return journal.append(kind, task, **fields)
    except Exception:
        return False


def emit_gate(task: str, stage: str, which: str, cmd: str, exit_code: int | None,
              passed: bool, duration_ms: int) -> bool:
    """One `gate` row. The command, its exit code and its duration are kept; its
    output never is."""
    outcome = ""
    if events is not None:
        outcome = events.GATE_PASS if passed else events.GATE_FAIL
    return emit("gate", task, stage=stage, pipeline=which, outcome=outcome,
                detail={"cmd": cmd, "exit": exit_code, "duration_ms": duration_ms})


def last_event_at(task: str, kind: str = "") -> str:
    """Timestamp of the newest journal row for `task` (of one kind), or ''. The
    supervisor's liveness report is the only reader; the journal's own dedupe of
    `merged` is the other. Neither decides a transition."""
    try:
        with _beside_run_store() as journal:
            return journal.last_at(task, kind) if journal is not None else ""
    except Exception:
        return ""


def _journal_write(task: str, before: dict | None, fields: dict, event: str,
                   reason: str) -> None:
    """Derive the journal rows of one set_fields() call from what changed.

    `before` is the run row as it was; a field that did not change writes nothing,
    so a repeated write (the merge park, a second approval) is not a second event.
    Never raises."""
    try:
        old = before or {}
        new_stage = fields.get("stage", old.get("stage") or "")
        old_stage = old.get("stage") or ""
        which = old.get("pipeline") or BUILD
        why = {"reason": reason} if reason else {}

        def put(kind: str, *, stage: str = "", from_stage: str = "", outcome: str = "",
                detail: dict | None = None) -> None:
            emit(kind, task, stage=stage or new_stage, from_stage=from_stage,
                 outcome=outcome, pipeline=which, detail=detail if detail is not None else why)

        if event == "rejected":
            put("rejected", from_stage=old_stage)
            # An attempt is counted from transition rows, and a rejection starts
            # one even when the run goes back to the stage it was already in.
            put("transition", from_stage=old_stage, detail={})
            return

        if event == "abandoned":
            # One row, no transition row: the run did not advance, it left.
            put("abandoned", from_stage=old_stage)
            return

        old_status = old.get("stage_status") or ""
        status = fields.get("stage_status")
        if status is not None:
            if status == ST_PAUSED and old_status != ST_PAUSED:
                put("paused", detail={"reason": fields.get("pause_reason") or reason,
                                      "kind": fields.get("pause_kind") or ""})
            elif old_status == ST_PAUSED and status != ST_PAUSED:
                put("resumed", detail={"status": status})
            elif status == ST_BLOCKED and old_status != ST_BLOCKED:
                put("blocked")

        if "stage" in fields and new_stage != old_stage:
            put("transition", from_stage=old_stage, detail={})

        waiting = fields.get("awaiting_human")
        if waiting and waiting != old.get("awaiting_human"):
            put("parked", outcome=str(waiting))

        for column, value in fields.items():
            if (column.endswith("_approved") and value and not old.get(column)):
                put("approved", outcome=column[:-len("_approved")], detail={})

        count = fields.get("continuations")
        if count and int(count) > int(old.get("continuations") or 0):
            put("continued", detail={"count": int(count)})

        if event in ("merged", "closed"):
            # One merge per task: a done run advanced again (the CEO re-running
            # advance.py) must not push the registration-to-merge figure out.
            if event == "closed" or not last_event_at(task, "merged"):
                put(event)
    except Exception:
        pass


def create_run(conn: sqlite3.Connection, task: str, task_type: str, stage: str,
               which: str = BUILD) -> dict:
    frozen = frozen_frontmatter(task)
    conn.execute(
        "INSERT INTO runs (task, type, stage, stage_status, awaiting_human, pipeline, updated, "
        f"{', '.join(frozen)}) VALUES (?, ?, ?, ?, '', ?, ?, {', '.join('?' * len(frozen))})",
        (task, task_type, stage, ST_IN_PROGRESS, which, now(), *frozen.values()),
    )
    conn.commit()
    emit("registered", task, stage=stage, pipeline=which, detail={"type": task_type})
    return get_run(conn, task)  # type: ignore[return-value]


def is_paused(run: dict | None) -> bool:
    """True for a run set aside by the pause command. A paused run is not driven
    and does not hold the working tree."""
    return (run or {}).get("stage_status") == ST_PAUSED


def is_abandoned(task: str) -> bool:
    """True when the CEO dropped the task (approve.py --abandon). Never raises: a
    missing or unreadable row answers False, the direction in which the debt
    readers still demand a doc and a stamp."""
    return _run_row(task).get("stage_status") == ST_ABANDONED


def is_parked(run: dict | None) -> bool:
    """True when no agent is being asked to do work on this run right now: it is
    paused, blocked, waiting on a human, or done. The one definition the Stop hook,
    advance.py and the supervisor share, so "is anything being driven" cannot be
    answered three ways.

    This is NOT the test for "holds the working tree": a blocked editing run still
    owns its dirty tree (stop_gate.decide()), so edit-serialization asks
    is_paused(), never this."""
    r = run or {}
    return bool(
        r.get("stage_status") in (ST_PAUSED, ST_BLOCKED)
        or r.get("awaiting_human")
        or r.get("stage") == "done"
    )


def run_pipeline(run: dict | None) -> str:
    return (run or {}).get("pipeline") or BUILD


def set_fields(conn: sqlite3.Connection, task: str, *, event: str = "", reason: str = "",
               **fields) -> None:
    """Write run columns, then journal what changed.

    `event` names a fact the columns alone do not carry: `rejected` (the CEO sent
    the run back), `merged` or `closed` (the run reached done because git says so).
    `reason` is the human text for a blocked, rejected, paused or closed row. Both
    only feed the journal; the UPDATE is exactly the `fields`."""
    if not fields:
        return
    try:
        before = get_run(conn, task)
    except Exception:
        before = None
    written = dict(fields)
    written["updated"] = now()
    cols = ", ".join(f"{k} = ?" for k in written)
    conn.execute(f"UPDATE runs SET {cols} WHERE task = ?", (*written.values(), task))
    conn.commit()
    _journal_write(task, before, fields, event, reason)


def task_dir(task: str) -> str | None:
    """Which of the three folders currently holds the task file."""
    for name, folder in TASK_DIRS.items():
        if (folder / f"{task}.md").is_file():
            return name
    return None


def _head_field(task: str, key: str) -> str:
    """One top-level frontmatter value of a task file, wherever the file sits,
    with surrounding quotes stripped; '' when the file, the field or its value is
    absent.

    The single reader for every task-file field (`repo`, `branch`, `epic`,
    `umbrella`, `subtasks`). Only the head is read: the frontmatter block, or the
    first 40 lines when the file has none, so the word `repo:` inside a task's
    prose cannot be mistaken for the field."""
    folder = task_dir(task)
    if folder is None:
        return ""
    try:
        text = (TASK_DIRS[folder] / f"{task}.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if text.startswith("---"):
        head = text.split("\n---", 1)[0]
    else:
        head = "\n".join(text.splitlines()[:40])
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", head)
    return m.group(1).strip().strip("'\"") if m else ""


# The task-file fields whose value decides closure or approval borrowing. They are
# copied into the run row when the run is created and read from there afterwards.
FROZEN_FIELDS = ("epic", "umbrella", "subtasks", "branch", "repo")

# `branch` and `repo` end up as git arguments (git_state rev-parse), so a value is
# frozen only when it is a plain ref-like name: no leading `-` (an option), no `..`.
# Anything else is stored empty, as if the task had declared nothing.
REF_FIELDS = ("branch", "repo")
REF_RE = re.compile(r"[A-Za-z0-9._/-]+")


def _plain_ref(value: str) -> bool:
    return bool(REF_RE.fullmatch(value)) and not value.startswith("-") and ".." not in value


def invalid_frozen_refs(task: str) -> list[str]:
    """The REF_FIELDS the task file declares with a value that freezing refuses."""
    return [key for key in REF_FIELDS
            if _head_field(task, key) and not _plain_ref(_head_field(task, key))]


def frozen_frontmatter(task: str) -> dict[str, str]:
    """The FROZEN_FIELDS values of the task file as it reads now, normalized: the
    umbrella flag is "true" or "", the subtask list is the bare task ids joined by a
    space, and a `branch` or `repo` that is not a plain ref name is "" (see
    invalid_frozen_refs). All empty when the file is absent."""
    return {
        "epic": _head_field(task, "epic"),
        "umbrella": "true" if _head_field(task, "umbrella").lower() == "true" else "",
        "subtasks": " ".join(re.findall(r"task-\d+", _head_field(task, "subtasks"))),
        **{key: "" if key in invalid_frozen_refs(task) else _head_field(task, key)
           for key in REF_FIELDS},
    }


def _run_row(task: str, conn: sqlite3.Connection | None = None) -> dict:
    """The task's run row; {} when there is none or the store cannot be read, which
    every caller treats as "nothing recorded"."""
    own = conn is None
    try:
        if own:
            conn = connect()
        return get_run(conn, task) or {}
    except Exception:
        return {}
    finally:
        if own and conn is not None:
            conn.close()


def task_repo(task: str) -> str | None:
    """The `repo:` value the task had when it was registered, None when blank or absent.

    `stack_gate.py` dispatches a stage's gate by it and
    `git_state.repos_for_task()` scopes merge detection by it, and the two must
    not be able to resolve the same task to different repos. Read from the run
    row, not the file, so rewriting `repo:` after the approval cannot move the
    merge check to a repo the diff never touched; a task with no run row yet
    (not registered, so nothing is frozen) reads the file. An unreadable store
    answers None, never the file: a corrupt run.db must not hand the choice of repo
    back to the file the agent being judged can write."""
    try:
        conn = connect()
        try:
            row = get_run(conn, task)
        finally:
            conn.close()
    except Exception:
        return None
    if row:
        return row.get("repo") or None
    return _head_field(task, "repo") or None


def task_branch(task: str) -> str:
    """The `branch:` value the task had when it was registered ('' when it had none
    or has no run row): the branch it declared it rides on when it is not its own
    `<type>/task-NNNN`. The commit gate reads it to decide whether a commit tagged
    with this task may be made on the checked-out branch. Read from the run row, not
    the file, so editing `branch:` after registration changes nothing."""
    return _run_row(task).get("branch") or ""


def move_task(task: str, to: str, abandoned: str = "") -> bool:
    """Move the task file into one of backlog/active/done.

    The folder is the state, so this single move IS the status change - there is
    no frontmatter field to keep in step with it. Only `done` stamps a date,
    because that is the one transition worth reading off the file itself. A task
    abandoned instead of merged (`abandoned` = the reason) gets an `abandoned:`
    line carrying the reason, and no `completed:` date: nothing was completed.

    Returns False and moves nothing when the destination file already exists:
    `done/<task>.md` is the record of a finished task and a move must not replace
    it. Fail-open: any error returns False without raising, so bookkeeping can
    never brick the pipeline."""
    dst_dir = TASK_DIRS.get(to)
    if dst_dir is None:
        return False
    try:
        src_name = task_dir(task)
        if src_name is None or src_name == to:
            return False
        src = TASK_DIRS[src_name] / f"{task}.md"
        text = src.read_text(encoding="utf-8", errors="replace")
        if to == "done" and abandoned:
            text = _stamp_abandoned(text, abandoned)
        elif to == "done":
            stamp = today()  # local date - the CEO reads this in the task file
            if re.search(r"(?m)^completed:", text):
                text = re.sub(r"(?m)^completed:.*$", f"completed: {stamp}", text, count=1)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{task}.md"
        # Exclusive create: a file already sitting at the destination is never
        # overwritten, whoever put it there. Exists-then-write would leave a gap
        # for the same overwrite; "x" refuses atomically (FileExistsError).
        try:
            with open(dst, "x", encoding="utf-8") as out:
                out.write(text)
        except FileExistsError:
            # A crash or lock between the create and the unlink below leaves both
            # copies, and every retry would land here. The same text at the
            # destination means the move already happened: finish it. Different
            # text is somebody else's file and stays refused (move_conflicts()).
            if not _same_task_text(dst.read_text(encoding="utf-8", errors="replace"), text):
                return False
        src.unlink()
        return True
    except Exception:
        return False


def _stamp_abandoned(text: str, reason: str) -> str:
    """`text` with an `abandoned: "<reason>"` line in its frontmatter (replacing
    one already there). The reason is a JSON string, which is also a valid YAML
    double-quoted scalar, so a colon or a quote in it cannot break the head."""
    line = f"abandoned: {json.dumps(reason, ensure_ascii=False)}"
    if re.search(r"(?m)^abandoned:", text):
        return re.sub(r"(?m)^abandoned:.*$", lambda _: line, text, count=1)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return f"{text[:end]}\n{line}{text[end:]}"
    return f"{line}\n{text}"


def _same_task_text(a: str, b: str) -> bool:
    """Equal task-file text, ignoring the `completed:` and `abandoned:` stamps a
    retry on another day, or with another reason, would write differently."""
    def bare(text: str) -> str:
        return re.sub(r"(?m)^(?:completed|abandoned):.*$", "", text)
    return bare(a) == bare(b)


def move_conflicts() -> list[str]:
    """Task ids whose file sits in done/ AND in backlog/ or active/ with
    different text: the one state move_task() refuses for good, so nothing
    resolves it without a human. Fail-open: never raises."""
    found: list[str] = []
    try:
        for done in sorted(TASK_DIRS["done"].glob("task-*.md")):
            for name in ("backlog", "active"):
                twin = TASK_DIRS[name] / done.name
                if twin.is_file() and not _same_task_text(
                        done.read_text(encoding="utf-8", errors="replace"),
                        twin.read_text(encoding="utf-8", errors="replace")):
                    found.append(done.stem)
                    break
    except Exception:
        pass
    return found


def move_task_to_done(task: str) -> bool:
    return move_task(task, "done")


def move_swarm_subtasks(task: str, conn: sqlite3.Connection | None = None) -> list[str]:
    """When an umbrella task has reached done/, move the subtask files it listed
    there too - only those still in backlog/ or active/ whose `epic:` equals the
    umbrella's. An unlisted subtask, or one from another epic, stays. The single
    sweep behind both merge paths: advance.py's merge confirmation and stop_gate's
    drift reconciler.

    Only when the umbrella's own run row has `commit_approved`, and the umbrella
    flag, epic and subtask list come from that row as recorded at registration:
    `active/` is not in the CEO's diff, so a subtask added to the file after the
    approval must not be swept without a merge. `conn` is the caller's open
    run-store connection; without one the store is opened here. A run that
    recorded no umbrella sweeps nothing.

    Fail-open: any error returns what moved so far (or []), never raises."""
    moved: list[str] = []
    try:
        row = _run_row(task, conn)
        epic = row.get("epic") or ""
        if (row.get("umbrella") != "true" or not epic or not row.get("commit_approved")
                or row.get("stage_status") == ST_ABANDONED):
            return moved
        for sub in re.findall(r"task-\d+", row.get("subtasks") or ""):
            if (sub != task and task_dir(sub) in ("backlog", "active")
                    and _head_field(sub, "epic") == epic and move_task(sub, "done")):
                moved.append(sub)
    except Exception:
        pass
    return moved


def covering_umbrella(task: str) -> str | None:
    """The done umbrella that covers a swarm subtask's handoff doc and memory
    stamp, or None.

    An umbrella task is documented once, for the whole swarm; its subtasks are
    swept to done/ with it and carry no doc of their own, so the debt readers
    (handoff.uncovered_done_tasks, update.unstamped_done_tasks) would otherwise
    demand 15 documents for one piece of work. A task is covered when an umbrella
    run row shares the task's non-empty `epic:` and listed the task, and that
    umbrella's file sits in done/, so a queued or active file naming a task cannot
    cancel that task's debt. The umbrella's flag, epic and list come from its run
    row as recorded at registration, not from its file: editing the file later
    cancels no debt. The row must also be at stage `done` with the commit approved:
    a registered umbrella whose file was merely moved to done/ by hand has been
    through neither the pipeline nor the CEO's diff, and covers nothing. An
    abandoned umbrella (task-0122) covers nothing either: abandon leaves the
    approval flags as they were, and work that never merged is documented by no one.

    Never raises: None means "not covered", the safe answer for a debt check."""
    try:
        epic = _head_field(task, "epic")
        if not epic:
            return None
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT task, subtasks FROM runs WHERE umbrella = 'true' AND epic = ? "
                "AND stage = 'done' AND commit_approved = 1 "
                "AND COALESCE(stage_status, '') != ? ORDER BY task",
                (epic, ST_ABANDONED)).fetchall()
        finally:
            conn.close()
        for umb, subtasks in rows:
            if (umb != task and task_dir(umb) == "done"
                    and task in re.findall(r"task-\d+", subtasks or "")):
                return umb
    except Exception:
        pass
    return None


def _blocked_tasks(live: list[dict]) -> list[str]:
    return [r["task"] for r in live if r["stage_status"] == ST_BLOCKED]


def blocked_notice(conn: sqlite3.Connection) -> str:
    """The sentence for the HUMAN when a session starts with a blocked run (the
    `systemMessage` counterpart of the model-facing line in _resume_text). Empty
    when nothing is blocked."""
    stuck = _blocked_tasks([r for r in all_runs(conn) if r["stage"] != "done"])
    if not stuck:
        return ""
    return (f"{', '.join(stuck)} parked BLOCKED and nothing is driving it. Decision owed by "
            f"you: fix the blocker so the agent can re-run it, or send the task back to "
            f"its first stage.")


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
    # A blocked run is the one exception to the line above, and it needs saying
    # out loud: advance.py refuses to move it, so the instruction to drive it is
    # wrong advice for exactly the run that most needs attention. The Stop hook
    # owns surfacing a blocked run mid-session (see stop_gate.decide); this is
    # the same fact reaching a session that STARTS with one already parked.
    stuck = _blocked_tasks(live)
    if stuck:
        lines.append(f"BLOCKED, needs the CEO before anything else: {', '.join(stuck)}. "
                     f"advance.py will not move a blocked run - raise it with the CEO "
                     f"(AskUserQuestion), then either fix the blocker or run approve.py "
                     f"--reject --reason \"<why>\" to send the task back.")
    # Same shape for a paused run: the line above would send the orchestrator to
    # advance a task the CEO deliberately set aside, and advance.py refuses it.
    paused = [r for r in live if is_paused(r)]
    if paused:
        who = "; ".join(f"{r['task']} ({r.get('pause_kind') or '?'}: "
                        f"{r.get('pause_reason') or 'no reason recorded'})" for r in paused)
        lines.append(f"PAUSED by the CEO, do not drive: {who}. Resume only when the CEO says: "
                     f"python .claude/tools/pipeline/approve.py --task <task> --resume.")
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

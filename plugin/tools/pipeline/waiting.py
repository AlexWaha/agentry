#!/usr/bin/env python3
"""Which runs wait for the User, across every lane (task-0153).

One definition of "this run owes the User something", shared by the Stop hook
(`stop_gate.owed_to_ceo`), the status line and the UserPromptSubmit reminder, so
the three cannot disagree. The dashboard has its own, looser list
(`views_pipeline._needs_you`): it must also work without the pipeline scripts, and it
lists any run with an `awaiting_human` value or a blocked status that is not paused or
abandoned, without the approval-recorded, done-stage and merge-mode rules below.

Standard library only, and no import of `state`, `stop_gate` or `pretool_gate`:
the status line and the reminder run on every refresh and every prompt, and the
heavy imports alone cost more than their whole time budget. Every store is
opened read-only (`mode=ro`) and never created.

Rules (the ones `owed_to_ceo` always had):
  - a paused run owes nothing: the User set it aside;
  - an approval already recorded is no longer owed;
  - a run at `done` owes nothing, except the merge wait, and that only in `pr`
    mode (in `solo` the pipeline merges locally);
  - the `blocked` value of `awaiting_human` is the Stop hook's own marker, not a
    checkpoint, so `owed_reason` (the Stop hook's filter) skips it; `needs_you` adds
    what the Stop hook reports elsewhere: a run whose STATUS is blocked owes an
    `unblock`;
  - an unknown `awaiting_human` value is reported as stored, not dropped.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths

AWAITING_BLOCKED = "blocked"
UNBLOCK = "unblock"
STATUS_PAUSED = "paused"
STATUS_BLOCKED = "blocked"
STATUS_ABANDONED = "abandoned"
DEFAULT_LANE = "default"
WORKFLOW_SOLO = "solo"

# Once the approval is recorded nothing is owed; the git action is the agent's.
APPROVED_FLAG = {"commit": "commit_approved", "push": "push_approved", "plan": "plan_approved"}

# Short names for what a parked run waits on. An unlisted reason is shown as it
# is stored, so a new park reason is visible rather than dropped.
LABEL = {
    "commit": "commit approval",
    "push": "push approval",
    "plan": "plan approval",
    "merge": "merge the PR",
    UNBLOCK: "unblock",
}

# run.db is the default lane, run.<lane>.db a named one. A backup such as
# run.db.bak-0124 does not match and is ignored.
LANE_DB_RE = re.compile(r"^run(\.[A-Za-z0-9_-]{1,32})?\.db$")
DB_TIMEOUT_SECONDS = 1
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def owed_reason(run: dict, solo: bool) -> str:
    """The `awaiting_human` value this run is parked on and the User owes, or ''.
    This is exactly the filter `stop_gate.owed_to_ceo` has always applied."""
    awaiting = run.get("awaiting_human") or ""
    if run.get("stage_status") == STATUS_PAUSED:
        return ""
    if not awaiting or awaiting == AWAITING_BLOCKED or run.get(APPROVED_FLAG.get(awaiting, "")):
        return ""
    if run.get("stage") == "done" and awaiting != "merge":
        return ""
    if awaiting == "merge" and solo:
        return ""
    return awaiting


def needs_you(run: dict, solo: bool) -> str:
    """What this run waits on from the User: a checkpoint name, `unblock`, or ''."""
    status = run.get("stage_status") or ""
    if status in (STATUS_PAUSED, STATUS_ABANDONED):
        return ""
    if status == STATUS_BLOCKED:
        return UNBLOCK
    return owed_reason(run, solo)


def is_solo(pipeline_path: Path | None = None) -> bool:
    """`solo` only when pipeline.json says so; anything else, including an
    unreadable file, is the collaborative default (as pretool_gate.workflow_mode)."""
    path = pipeline_path or paths.PROJECT_ROOT / ".agentry" / "pipeline.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = data.get("workflow", {}) if isinstance(data, dict) else {}
        mode = str(cfg.get("mode") or "").strip().lower() if isinstance(cfg, dict) else ""
    except (OSError, ValueError, RecursionError):
        return False
    return mode == WORKFLOW_SOLO


def lane_dbs(state_dir: Path) -> list[tuple[str, Path]]:
    """(lane, path) for every existing run database. A list keyed by file, not a
    dict keyed by lane: `run.default.db` is a real store of a lane named `default`
    (PIPELINE_LANE=default) and must not replace or hide `run.db`."""
    found: list[tuple[str, Path]] = []
    try:
        names = sorted(p.name for p in state_dir.iterdir())
    except OSError:
        return found
    for name in names:
        match = LANE_DB_RE.match(name)
        if match and (state_dir / name).is_file():
            found.append((match.group(1)[1:] if match.group(1) else DEFAULT_LANE, state_dir / name))
    return found


def read_runs(db: Path) -> list[dict]:
    """Every run row of one store, or [] when it cannot be read. Read-only."""
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=DB_TIMEOUT_SECONDS)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM runs")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def clean(value: object) -> str:
    """A run.db value as one line of plain text. The stores are written by the
    pipeline scripts, but a value ends up in a terminal (status line) and in the
    model's context (reminder), so control characters, escape sequences included,
    are replaced once here."""
    return CONTROL_RE.sub(" ", str(value or ""))


def waiting_runs(state_dir: Path | None = None, solo: bool | None = None) -> list[dict]:
    """[{lane, task, stage, kind, since}] for every run waiting on the User in any
    lane, oldest first (then lane, then task). `kind` is the checkpoint name or
    `unblock`."""
    state_dir = state_dir or paths.PROJECT_ROOT / ".agentry" / "state"
    solo = is_solo() if solo is None else solo
    out = []
    for lane, db in lane_dbs(state_dir):
        for run in read_runs(db):
            kind = needs_you(run, solo)
            if kind:
                out.append({"lane": lane, "task": clean(run.get("task")), "stage": clean(run.get("stage")),
                            "kind": clean(kind), "since": clean(run.get("updated"))})
    out.sort(key=lambda r: (r["since"], r["lane"], r["task"]))
    return out

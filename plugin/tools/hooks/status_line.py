#!/usr/bin/env python3
"""Status line - where the pipeline stands, always visible (FR-52).

One line, read from run.db and the lane and the three dials:

    task-0118 | stage review | owed: commit approval | lane default [agentry]
    | mode build | approvals manual | swarm off

task, stage and owed action come from the run the User should look at first: a
run parked on the User, else a run being driven, else any run that is not done. With
nothing in flight the line says so and still shows the dials.

While any run in ANY lane waits for the User (a checkpoint approval or an unblock; a
paused run does not count), a first segment names the count and the oldest waiting
run, `Needs you: 2 (task-0128 plan)`, and is absent otherwise (task-0153). The
rules live in `waiting.py`, shared with the Stop hook and the per-prompt reminder.

Fails open (NFR-4): the line is cosmetic, so nothing here may raise or print to
stderr. run.db is opened read-only (`mode=ro`) and never created, so a status
refresh cannot hold a write lock against the pipeline scripts or invent a store.
An unreadable store renders a degraded line, not an error.

Claude Code sends a JSON document on stdin; none of it is needed, the state
lives in the project.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "pipeline"))

import waiting  # noqa: E402  (after the sys.path insert)

SEP = " | "
YELLOW = "\x1b[1;33m"
RESET = "\x1b[0m"

OWED_LABEL = waiting.LABEL

# pipeline.json is read once per render, not once per run the sort key looks at.
_SOLO: dict[str, bool] = {}


def solo() -> bool:
    import state

    if "solo" not in _SOLO:
        _SOLO["solo"] = waiting.is_solo(state.PIPELINE_PATH)
    return _SOLO["solo"]


def read_runs() -> list[dict] | None:
    """Every run, or None when run.db exists but cannot be read. A store that
    does not exist yet is simply no runs."""
    import state

    if not state.DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"{state.DB_PATH.as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM runs ORDER BY task").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def owed_label(run: dict) -> str:
    """What the User owes this run, or '' when nothing. The rules for when a park
    is owed live in waiting.py, shared with the Stop hook (stop_gate.owed_to_ceo),
    so the line and the hook cannot disagree. A paused run owes nothing: the User
    set it aside and the stage shows (paused)."""
    kind = waiting.needs_you(run, solo())
    return OWED_LABEL.get(kind, kind)


def needs_you_segment() -> str:
    """`Needs you: N (task-0128 plan)` over every lane, or '' when nothing waits."""
    import state

    runs = waiting.waiting_runs(state.DB_PATH.parent, solo())
    if not runs:
        return ""
    first = runs[0]
    return f"{YELLOW}Needs you: {len(runs)} ({first['task']} {first['kind']}){RESET}"


def focal_run(runs: list[dict]) -> tuple[dict, int] | None:
    """The run to describe and how many other unfinished runs there are."""
    import state

    live = [r for r in runs if r.get("stage") != "done"]
    if not live:
        return None
    live.sort(key=lambda r: (not owed_label(r), state.is_parked(r), r["task"]))
    return live[0], len(live) - 1


def run_fields(runs: list[dict] | None) -> list[str]:
    if runs is None:
        return ["run.db unreadable"]
    found = focal_run(runs)
    if found is None:
        return ["no run in flight"]
    run, others = found
    status = run.get("stage_status") or ""
    stage = f"stage {run['stage']}" + (f" ({status})" if status not in ("", "in_progress") else "")
    owed = owed_label(run)
    owed_text = f"owed: {YELLOW}{owed}{RESET}" if owed else "owed: none"
    return [run["task"] + (f" +{others}" if others else ""), stage, owed_text]


def dial_fields() -> list[str]:
    """lane, mode, approvals, swarm. Each fails to '?' on its own."""
    fields = []

    def add(label: str, get) -> None:
        try:
            fields.append(f"{label} {get()}")
        except Exception:
            fields.append(f"{label} ?")

    def lane() -> str:
        import state

        name, repo = state.lane_name(), state.lane_repo()
        return f"{name} [{repo}]" if repo else name

    def mode_name() -> str:
        import mode

        return mode.read()

    def approvals_level() -> str:
        import approvals

        return approvals.read()

    def swarm_flag() -> str:
        import swarm

        return swarm.read()

    add("lane", lane)
    add("mode", mode_name)
    add("approvals", approvals_level)
    add("swarm", swarm_flag)
    return fields


def render() -> str:
    _SOLO.clear()
    try:
        runs = read_runs()
    except Exception:
        runs = None
    try:
        head = run_fields(runs)
    except Exception:
        head = ["run state unavailable"]
    try:
        head = [needs_you_segment(), *head]
    except Exception:
        pass  # cosmetic: the rest of the line still renders
    return SEP.join([f for f in head if f] + dial_fields())


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        print(render())
    except Exception:
        print("pipeline status unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

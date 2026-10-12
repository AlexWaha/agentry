#!/usr/bin/env python3
"""Status line - where the pipeline stands, always visible (FR-52).

One line, read from run.db and the lane and the three dials:

    task-0118 | stage review | owed: commit approval | lane default [agentry]
    | mode build | approvals manual | swarm off

task, stage and owed action come from the run the CEO should look at first: a
run parked on him, else a run being driven, else any run that is not done. With
nothing in flight the line says so and still shows the dials.

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

SEP = " | "
YELLOW = "\x1b[1;33m"
RESET = "\x1b[0m"

# Short names for what a parked run waits on. An unlisted reason is shown as it
# is stored, so a new park reason is visible rather than dropped.
OWED_LABEL = {
    "commit": "commit approval",
    "push": "push approval",
    "plan": "plan approval",
    "merge": "merge the PR",
}


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
    """What the CEO owes this run, or '' when nothing. The rules for when a park
    is his live in stop_gate.owed_to_ceo; asking it keeps the line and the Stop
    hook from disagreeing. A paused run owes nothing: the CEO set it aside and
    the stage shows (paused)."""
    import state

    if state.is_paused(run):
        return ""
    if run.get("stage_status") == state.ST_BLOCKED:
        return "unblock"
    if not run.get("awaiting_human"):
        return ""
    try:
        import stop_gate

        if not stop_gate.owed_to_ceo([run]):
            return ""
    except Exception:
        pass  # the checker is unavailable: show the stored reason as it is
    return OWED_LABEL.get(run["awaiting_human"], run["awaiting_human"])


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
    try:
        runs = read_runs()
    except Exception:
        runs = None
    try:
        head = run_fields(runs)
    except Exception:
        head = ["run state unavailable"]
    return SEP.join(head + dial_fields())


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

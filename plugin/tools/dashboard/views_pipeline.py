"""Pipeline read model: status, runs, tasks, epics, events and metrics (spec-0002 FR-17 to FR-27,
FR-123).

Every function is `view(ctx, params, *path_ids) -> (data, meta)` and only reads: stores are
opened through stores.store (mode=ro, closed in finally), files are read, nothing is written.
The harness readers it reuses (state.stages, handoff.owed, update.unstamped_done_tasks, ...)
are the allowlisted pure ones, so the debt shown is exactly what the Stop hook enforces.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

import stores

stores.ensure_tool_paths()

import approvals  # noqa: E402  (harness modules: the tool folders were put on sys.path above)
import events  # noqa: E402
import handoff  # noqa: E402
import mode  # noqa: E402
import state  # noqa: E402
import supervisor  # noqa: E402
import update  # noqa: E402

CHECKPOINT_TEXT = {
    "plan": "Approve the plan before any code is written",
    "commit": "Read the diff and approve the commit",
    "push": "Approve the push",
    "trunk_push": "Approve the one-shot trunk push",
}
TYPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
EVENT_TASK_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
LANE_RE = stores.LANE_NAME_RE
DETAIL_KEYS = ("reason", "duration_ms", "exit", "cmd")
EVENT_COLUMNS = "id, at, lane, task, pipeline, kind, stage, from_stage, outcome, via, detail"
SLOWEST_GATES = 10
LATEST_REJECTIONS = 5
EVENT_CAP_IN_DETAIL = 200


class _NoRuns:
    """Stands in for a run store that does not exist: no row, no umbrella. The debt
    readers get this instead of None because None makes them open - and so create -
    run.db."""

    class _Cursor:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    def execute(self, *_args):
        return self._Cursor()


# --------------------------------------------------------------------------- shared readers

def _detail(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _event(row) -> dict:
    """One Event. `detail` carries only the keys of the contract, the same filter watch.py
    applies, so a row pushed over the WebSocket and the same row fetched here are equal."""
    detail = _detail(row["detail"])
    return {"id": int(row["id"]), "at": stores.iso_z(row["at"]), "lane": row["lane"] or "",
            "task": row["task"] or "", "pipeline": row["pipeline"] or "", "kind": row["kind"] or "",
            "stage": row["stage"] or "", "from_stage": row["from_stage"] or "",
            "outcome": row["outcome"] or "", "via": row["via"] or "",
            "detail": {k: detail[k] for k in DETAIL_KEYS if k in detail}}


def _gate_counts(conn, task: str | None = None) -> dict[tuple[str, str], tuple[int, int]]:
    """(task, stage) -> (passes, failures) of `gate` events."""
    if conn is None or not stores.has_table(conn, "events"):
        return {}
    sql = "SELECT task, stage, outcome, COUNT(*) AS n FROM events WHERE kind = 'gate'"
    args: tuple = ()
    if task is not None:
        sql += " AND task = ?"
        args = (task,)
    out: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in conn.execute(sql + " GROUP BY task, stage, outcome", args):
        slot = out[(row["task"], row["stage"])]
        if row["outcome"] == events.GATE_PASS:
            slot[0] += row["n"]
        elif row["outcome"] == events.GATE_FAIL:
            slot[1] += row["n"]
    return {key: (v[0], v[1]) for key, v in out.items()}


def _supervised(root: Path, lane: str) -> dict:
    name = "supervisor.json" if lane == stores.DEFAULT_LANE else f"supervisor.{lane}.json"
    tasks = stores.read_json(stores.state_dir(root) / name).get("tasks")
    return tasks if isinstance(tasks, dict) else {}


def _stages(pipeline: dict, which: str, task_type: str, current: str, task: str,
            gates: dict) -> list[dict]:
    defs = state.stages(pipeline, which or state.BUILD)
    names = [d.get("name", "") for d in defs]
    here = names.index(current) if current in names else -1
    out = []
    for i, definition in enumerate(defs):
        name = definition.get("name", "")
        if task_type and task_type in (definition.get("skip_for_types") or []):
            where = "skipped"
        elif here < 0 or i > here:
            where = "next"
        else:
            where = "done" if i < here else "now"
        passed, failed = gates.get((task, name), (0, 0))
        out.append({"name": name, "human": bool(definition.get("checkpoints")), "state": where,
                    "pass": passed, "fail": failed})
    return out


def _needs_you(row: dict, reasons: dict) -> dict | None:
    status = row.get("stage_status") or ""
    if status in (state.ST_PAUSED, state.ST_ABANDONED):
        return None
    awaiting = row.get("awaiting_human") or ""
    if awaiting:
        kind = awaiting
    elif status == state.ST_BLOCKED:
        kind = "blocked"
    else:
        return None
    reason = reasons.get(row["task"]) or CHECKPOINT_TEXT.get(kind, "")
    return {"kind": kind, "since": stores.iso_z(row.get("updated")), "reason": reason}


def _build_run(row: dict, lane: str, pipeline: dict, index: dict, gates: dict, supervised: dict,
               reasons: dict) -> dict:
    task = row["task"]
    which = row.get("pipeline") or state.BUILD
    entry = supervised.get(task)
    sub_ids = stores.TASK_REF_RE.findall(row.get("subtasks") or "")
    return {
        "task": task,
        "title": index[task]["title"] if task in index else "",
        "lane": lane,
        "pipeline": which,
        "type": row.get("type") or "",
        "stage": row.get("stage") or "",
        "stage_status": row.get("stage_status") or "",
        "awaiting_human": row.get("awaiting_human") or "",
        "retries": int(row.get("retries") or 0),
        "epic": row.get("epic") or "",
        "worktree": row.get("worktree") or "",
        "umbrella": row.get("umbrella") == "true",
        "subtasks": [stores.task_ref(t, index) for t in sub_ids],
        "updated": stores.iso_z(row.get("updated")),
        "stages": _stages(pipeline, which, row.get("type") or "", row.get("stage") or "", task, gates),
        "needs_you": _needs_you(row, reasons),
        "supervisor": ({"label": str(entry.get("last_label") or ""),
                        "seen": stores.epoch_iso(entry.get("seen"))}
                       if isinstance(entry, dict) else None),
    }


def _stop_reasons(conn, tasks: list[str]) -> dict[str, str]:
    """The reason of the newest `blocked` / `parked` row per task, for the runs that need the
    User. A newer row without a reason hides an older one ("" falls back to the checkpoint text)."""
    if conn is None or not tasks or not stores.has_table(conn, "events"):
        return {}
    marks = ",".join("?" * len(tasks))
    out: dict[str, str] = {}
    for row in conn.execute(f"SELECT task, detail FROM events WHERE kind IN ('blocked', 'parked') "
                            f"AND task IN ({marks}) ORDER BY id", tasks):
        reason = _detail(row["detail"]).get("reason")
        out[row["task"]] = reason if isinstance(reason, str) else ""
    return out


def read_runs(ctx: stores.Ctx, lane_filter: str = "", only_task: str = "") -> list[dict]:
    """Every run of every lane as a Run, newest `updated` first then task ascending."""
    root = ctx.project_root
    pipeline = stores.read_pipeline(root)
    index = stores.task_index(stores.scan_tasks(root))
    raw: list[tuple[str, dict]] = []
    for lane, path in stores.lane_db_paths(root).items():
        if lane_filter and lane != lane_filter:
            continue
        with stores.store(path) as conn:
            if conn is not None and stores.has_table(conn, "runs"):
                sql = "SELECT * FROM runs" + (" WHERE task = ?" if only_task else "")
                raw += [(lane, dict(r)) for r in conn.execute(sql, (only_task,) if only_task else ())]
    needing = [r["task"] for _, r in raw if _needs_you(r, {})]
    with stores.store(stores.events_db(root)) as conn:
        gates = _gate_counts(conn, only_task or None)
        reasons = _stop_reasons(conn, needing)
    supervised = {lane: _supervised(root, lane) for lane in {lane for lane, _ in raw}}
    runs = [_build_run(r, lane, pipeline, index, gates, supervised[lane], reasons) for lane, r in raw]
    runs.sort(key=lambda r: r["task"])
    runs.sort(key=lambda r: r["updated"], reverse=True)
    return runs


# --------------------------------------------------------------------------- /status, /runs

def _supervisor_state(root: Path, lane_names: list[str], pipeline: dict) -> dict:
    block = pipeline.get("supervisor")
    if isinstance(block, dict) and block.get("enabled") is False:
        return {"state": "disabled", "runs": 0}
    running, supervised = False, 0
    for lane in lane_names:
        suffix = "" if lane == stores.DEFAULT_LANE else f".{lane}"
        supervised += len(_supervised(root, lane))
        lock = stores.read_json(stores.state_dir(root) / f"supervisor{suffix}.lock")
        pid = lock.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and supervisor.pid_alive(pid):
            running = True
    return {"state": "running" if running else "stopped", "runs": supervised}


def status(ctx: stores.Ctx, params: stores.Params):
    params.check()
    root = ctx.project_root
    pipeline = stores.read_pipeline(root)
    tasks = stores.scan_tasks(root)
    names = stores.lane_names(root)
    declared = stores.declared_lanes(root)
    known_modes = mode.modes()
    lanes = []
    for name in names:
        dial_mode = stores.read_dial(root, "mode", name)
        level = stores.read_dial(root, "approvals", name)
        lanes.append({
            "name": name,
            "repo": declared.get(name, ""),
            "mode": dial_mode if dial_mode in known_modes else mode.default(),
            "approvals": level if level in approvals.LEVELS else approvals.LEVELS[0],
            "swarm": stores.read_dial(root, "swarm", name) == "on",
        })
    with stores.store(stores.lane_db_paths(root).get(stores.DEFAULT_LANE, root / "absent.db")) as conn:
        if conn is not None:
            stores.has_table(conn, "runs")  # a locked store raises here, once, and the route is a 503
        target = conn if conn is not None else _NoRuns()
        debt = {"handoffs": handoff.owed(target), "memory": update.unstamped_done_tasks(target)}
    workflow = pipeline.get("workflow") if isinstance(pipeline.get("workflow"), dict) else {}
    trees = pipeline.get("worktrees") if isinstance(pipeline.get("worktrees"), dict) else {}
    max_parallel = trees.get("max_parallel")
    plugin = stores.plugin_manifest(ctx.plugin_root)
    return {
        "project": root.name,
        "plugin_version": str(plugin.get("version") or ""),
        "protocol": 1,
        "read_only": True,
        "workflow": {
            "mode": "solo" if str(workflow.get("mode") or "").strip().lower() == "solo" else "pr",
            "push_needs_approval": workflow.get("push_needs_approval", True) is not False,
        },
        "lanes": lanes,
        "worktrees": {
            "enabled": trees.get("enabled") is True,
            "max_parallel": max_parallel if isinstance(max_parallel, int)
            and not isinstance(max_parallel, bool) and max_parallel >= 1 else 1,
        },
        "supervisor": _supervisor_state(root, names, pipeline),
        "debt": debt,
        "counts": {
            "backlog": sum(t["folder"] == "backlog" for t in tasks),
            "active": sum(t["folder"] == "active" for t in tasks),
            "done": sum(t["folder"] == "done" for t in tasks),
            "hidden_superseded": sum(bool(t["superseded_by"]) for t in tasks),
        },
    }, None


def runs(ctx: stores.Ctx, params: stores.Params):
    lane = params.match("lane", LANE_RE, "must be a lane name")
    params.check()
    return read_runs(ctx, lane), None


# --------------------------------------------------------------------------- /tasks

def _summary(task: dict, index: dict) -> dict:
    return {
        "id": task["id"], "title": task["title"], "type": task["type"], "priority": task["priority"],
        "epic": task["epic"], "spec": task["spec"], "assignee": task["assignee"],
        "folder": task["folder"], "created": task["created"], "completed": task["completed"],
        "blocked_on": task["blocked_on"], "superseded_by": task["superseded_by"],
        "depends_on": [stores.task_ref(t, index) for t in task["depends_on"]],
    }


def tasks(ctx: stores.Ctx, params: stores.Params):
    folder = params.choice("folder", stores.FOLDERS)
    epic = params.text("epic")
    task_type = params.match("type", TYPE_RE, "must be letters, digits, _ or - (1 to 32)")
    needle = params.text("q").lower()
    include = params.boolean("include_superseded")
    limit, offset = params.page(25, 500)
    params.check()

    everything = stores.scan_tasks(ctx.project_root)
    index = stores.task_index(everything)

    def narrowed(t: dict, ignore_type: bool = False) -> bool:
        return ((not epic or t["epic"] == epic)
                and (ignore_type or not task_type or t["type"] == task_type)
                and (not needle or needle in t["id"] or needle in t["title"].lower()))

    base = [t for t in everything if narrowed(t)]
    hidden = 0 if include else sum(bool(t["superseded_by"]) for t in base
                                   if not folder or t["folder"] == folder)
    visible = base if include else [t for t in base if not t["superseded_by"]]
    rows = [t for t in visible if not folder or t["folder"] == folder]
    rows.sort(key=stores.task_sort_key, reverse=folder in ("", "done"))
    types = sorted({t["type"] for t in everything
                    if narrowed(t, ignore_type=True) and (include or not t["superseded_by"])
                    and (not folder or t["folder"] == folder) and t["type"]})
    meta = {
        "total": len(rows), "limit": limit, "offset": offset,
        "hidden_superseded": hidden,
        "facets": {"folders": {f: sum(t["folder"] == f for t in visible) for f in stores.FOLDERS},
                   "types": types},
    }
    return [_summary(t, index) for t in rows[offset:offset + limit]], meta


def _stamp_time(root: Path, task: str) -> str | None:
    data = stores.read_json(stores.state_dir(root) / "memory" / f"{task}.json")
    return stores.iso_z(data.get("stamped_at")) or None


def _handoff_status(task: str) -> dict | None:
    path = handoff.handoff_path(task)
    if not path.is_file():
        return None
    try:
        problems = handoff.validate_handoff(path, task)
    except Exception:
        problems = ["the handoff document could not be validated"]
    return {"valid": not problems, "problems": problems}


def task_detail(ctx: stores.Ctx, params: stores.Params, task_id: str):
    stores.path_id(task_id)
    params.check()
    root = ctx.project_root
    found = stores.find_task_file(root, task_id)
    if found is None:
        raise stores.not_found("No such task")
    everything = stores.scan_tasks(root)
    index = stores.task_index(everything)
    task = next((t for t in everything if t["id"] == task_id and t["folder"] == found[0]), None)
    if task is None:  # the file moved between the lookup and the listing
        raise stores.not_found("No such task")
    _, body = stores.split_frontmatter(stores.read_capped(found[1]))
    body, body_truncated = stores.cap_text(body)

    run = next(iter(read_runs(ctx, only_task=task_id)), None)
    shown: list[dict] = []
    truncated = False
    gate_runs = gate_failures = rejections = 0
    with stores.store(stores.events_db(root)) as conn:
        if conn is not None and stores.has_table(conn, "events"):
            newest = conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events WHERE task = ? ORDER BY id DESC LIMIT ?",
                (task_id, EVENT_CAP_IN_DETAIL + 1)).fetchall()
            truncated = len(newest) > EVENT_CAP_IN_DETAIL
            shown = [_event(r) for r in reversed(newest[:EVENT_CAP_IN_DETAIL])]
            for row in conn.execute("SELECT kind, outcome, COUNT(*) AS n FROM events WHERE task = ? "
                                    "AND kind IN ('gate', 'rejected') GROUP BY kind, outcome",
                                    (task_id,)):
                if row["kind"] == "rejected":
                    rejections += row["n"]
                else:
                    gate_runs += row["n"]
                    gate_failures += row["n"] if row["outcome"] == events.GATE_FAIL else 0
    return {
        **_summary(task, index),
        "phase": task["phase"],
        "repo": task["repo"],
        "umbrella": task["umbrella"],
        "subtasks": [stores.task_ref(t, index) for t in task["subtasks"]],
        "dependents": [stores.task_ref(t["id"], index)
                       for t in sorted(everything, key=stores.task_sort_key)
                       if task_id in t["depends_on"] and t["id"] != task_id],
        "body": body,
        "body_truncated": body_truncated,
        "run": run,
        "stages": run["stages"] if run else [],
        "events": shown,
        "events_truncated": truncated,
        "gate_runs": gate_runs,
        "gate_failures": gate_failures,
        "rejections": rejections,
        "handoff": _handoff_status(task_id),
        "memory_stamped_at": _stamp_time(root, task_id),
    }, None


def epics(ctx: stores.Ctx, params: stores.Params):
    params.check()
    index = stores.task_index(stores.scan_tasks(ctx.project_root))
    return [{"id": e["id"], "title": e["title"], "status": e["status"],
             "tasks": [stores.task_ref(t, index) for t in e["tasks"]]}
            for e in stores.scan_epics(ctx.project_root)], None


# --------------------------------------------------------------------------- /events

def events_list(ctx: stores.Ctx, params: stores.Params):
    task = params.match("task", EVENT_TASK_RE, "must be letters, digits or - (1 to 32)").lower()
    kind = params.choice("kind", events.KINDS)
    result = params.choice("result", (events.GATE_PASS, events.GATE_FAIL))
    limit, offset = params.page(25, 500)
    params.check()
    where, args = [], []
    if task:
        where.append("instr(lower(task), ?) > 0")
        args.append(task)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if result:
        where.append("outcome = ?")
        args.append(result)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows, total, latest = [], 0, 0
    with stores.store(stores.events_db(ctx.project_root)) as conn:
        if conn is not None and stores.has_table(conn, "events"):
            total = conn.execute(f"SELECT COUNT(*) FROM events{clause}", args).fetchone()[0]
            latest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
            rows = [_event(r) for r in conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*args, limit, offset))]
    return rows, {"total": total, "limit": limit, "offset": offset, "latest_id": latest,
                  "kinds": sorted(events.KINDS)}


# --------------------------------------------------------------------------- /metrics

def _seconds(start: str, end: str) -> float | None:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (b - a).total_seconds()


def _spread(values: list) -> tuple:
    """(median, nearest-rank p90, max) of a non-empty list."""
    ordered = sorted(values)
    return (statistics.median(ordered), ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)],
            ordered[-1])


def _stage_rank(stage_order: list[str]):
    return lambda name: (stage_order.index(name) if name in stage_order else len(stage_order), name)


def metrics(ctx: stores.Ctx, params: stores.Params):
    since = params.date("since")
    days = params.integer("days", 14, 1, 90)
    if since and params.has("days"):
        params.reject("since", "cannot be combined with days")
        params.reject("days", "cannot be combined with since")
    params.check()
    cutoff = (f"{since}T00:00:00Z" if since else
              (stores.utc_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    rows, lead_starts = [], {}
    with stores.store(stores.events_db(ctx.project_root)) as conn:
        if conn is not None and stores.has_table(conn, "events"):
            rows = conn.execute(f"SELECT {EVENT_COLUMNS} FROM events WHERE at >= ? ORDER BY id",
                                (cutoff,)).fetchall()
            merged_tasks = sorted({r["task"] for r in rows if r["kind"] == "merged"})
            for task in merged_tasks:
                lead_starts[task] = conn.execute("SELECT MIN(at) FROM events WHERE task = ?",
                                                 (task,)).fetchone()[0]

    kinds = Counter(r["kind"] for r in rows)
    gate_ms: dict[str, list] = defaultdict(list)
    gate_runs: Counter = Counter()
    gate_failures: Counter = Counter()
    slowest, per_day = [], defaultdict(lambda: {"gates": 0, "gate_failures": 0, "merged": 0, "rejected": 0})
    entries: dict[str, list] = defaultdict(list)
    rejected, merged_at = [], {}
    for row in rows:
        day = per_day[row["at"][:10]]
        kind = row["kind"]
        if kind == "gate":
            failed = row["outcome"] == events.GATE_FAIL
            gate_runs[row["stage"]] += 1
            gate_failures[row["stage"]] += failed
            day["gates"] += 1
            day["gate_failures"] += failed
            ms = _detail(row["detail"]).get("duration_ms")
            if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                gate_ms[row["stage"]].append(ms)
                slowest.append({"task": row["task"], "stage": row["stage"], "ms": ms,
                                "passed": row["outcome"] == events.GATE_PASS,
                                "at": stores.iso_z(row["at"]), "_id": row["id"]})
        elif kind == "merged":
            day["merged"] += 1
            merged_at.setdefault(row["task"], row["at"])
        elif kind == "rejected":
            day["rejected"] += 1
            rejected.append(row)
        if kind in ("registered", "transition"):
            entries[row["task"]].append(row)

    order = state.stage_names(stores.read_pipeline(ctx.project_root))
    rank = _stage_rank(order)
    gates = []
    for stage in sorted(gate_runs, key=rank):
        median, p90, longest = _spread(gate_ms[stage]) if gate_ms[stage] else (0, 0, 0)
        gates.append({"stage": stage, "runs": gate_runs[stage], "failures": gate_failures[stage],
                      "median_ms": median, "p90_ms": p90, "max_ms": longest})
    slowest.sort(key=lambda g: (-g["ms"], g["_id"]))
    dwell_s: dict[str, list] = defaultdict(list)
    for task_entries in entries.values():
        for here, after in pairwise(task_entries):
            seconds = _seconds(here["at"], after["at"])
            if seconds is not None and seconds >= 0:
                dwell_s[here["stage"]].append(seconds)
    dwell = []
    for stage in sorted(dwell_s, key=rank):
        median, p90, longest = _spread(dwell_s[stage])
        dwell.append({"stage": stage, "n": len(dwell_s[stage]), "median_s": median,
                      "p90_s": p90, "max_s": longest})
    leads = [s for task, at in merged_at.items()
             if (s := _seconds(lead_starts.get(task) or "", at)) is not None and s >= 0]
    return {
        "span": f"{rows[0]['at']}/{rows[-1]['at']}" if rows else "",
        "events_total": len(rows),
        "kinds": dict(sorted(kinds.items())),
        "gates": gates,
        "slowest": [{k: v for k, v in g.items() if k != "_id"} for g in slowest[:SLOWEST_GATES]],
        "dwell": dwell,
        "per_day": [{"day": day, **counts} for day, counts in sorted(per_day.items())],
        "rejections": {
            "total": len(rejected),
            "latest": [{"task": r["task"], "at": stores.iso_z(r["at"]), "from_stage": r["from_stage"],
                        "stage": r["stage"], "reason": str(_detail(r["detail"]).get("reason") or "")}
                       for r in reversed(rejected[-LATEST_REJECTIONS:])],
        },
        "merges_total": kinds["merged"],
        "lead_time_s": statistics.median(leads) if leads else None,
    }, None

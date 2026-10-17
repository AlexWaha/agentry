"""Change watcher for the dashboard: one tick per second while a client is connected.

A tick compares the stores and files the dashboard shows with what the last tick
saw and broadcasts what moved (spec-0002 FR-49 to FR-53):
- events.db rows past the cursor go out as `events.appended` (up to 200), a
  burst or a cursor regression goes out as `resync`;
- every other source maps to topics and goes out as one `invalidate`.

Read-only by construction: stores open through stores.ro_connect and close in the
same function (FR-3), files are only listed and stat-ed, nothing is written, and
there is no OS file-watch API and no PRAGMA data_version (NG-5). With no client
the tick returns before it touches anything (FR-49, NFR-7).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import stores
import ws

TICK_SECONDS = 1.0  # module global so a test can scale it
EVENTS_BATCH = 200  # FR-51: more new rows than this is a burst

_EVENT_KEYS = ("id", "at", "lane", "task", "pipeline", "kind", "stage", "from_stage", "outcome", "via")
_DETAIL_KEYS = ("reason", "duration_ms", "exit", "cmd")
_LANE = r"(?:\.[A-Za-z0-9_-]{1,32})?"
# The dial files and the supervisor record, per lane; supervisor.log and the like stay out.
_STATUS_FILE = re.compile(rf"^(?:(?:mode|approvals|swarm){_LANE}|supervisor{_LANE}\.json)$")
_UNREAD = object()  # a source that could not be read yet: never compared

_lock = threading.Lock()
_state: _State | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


class _State:
    def __init__(self, cursor: int, prints: dict[str, Any]) -> None:
        self.cursor = cursor
        self.prints = prints


# ---------------------------------------------------------------- fingerprints
def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _listing(directory: Path, keep: Callable[[str], bool] = lambda name: True) -> tuple:
    """(name, mtime_ns, size) of the regular files in `directory`, sorted."""
    rows = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if keep(entry.name) and entry.is_file():
                        st = entry.stat()
                        rows.append((entry.name, st.st_mtime_ns, st.st_size))
                except OSError:
                    continue  # vanished while listing
    except OSError:
        return ()
    return tuple(sorted(rows))


def _skill_listing(skills_dir: Path) -> tuple:
    """(skill folder, SKILL.md stat) for each folder of a skills directory."""
    try:
        with os.scandir(skills_dir) as entries:
            rows = [(e.name, _stat(Path(e.path) / "SKILL.md")) for e in entries if e.is_dir()]
    except OSError:
        return ()
    return tuple(sorted(rows, key=lambda row: row[0]))


def _runs(ctx: Any) -> dict[str, str]:
    """Per lane, sha1 over the runs table in primary-key order. A store that
    cannot be read raises, and the caller skips the source for this tick."""
    out = {}
    for lane, path in sorted(stores.lane_db_paths(ctx.project_root).items()):
        with stores.store(path) as con:
            if con is None:
                continue
            digest = hashlib.sha1()
            for row in con.execute("SELECT * FROM runs ORDER BY task"):
                digest.update(repr(tuple(row)).encode("utf-8"))
            out[lane] = digest.hexdigest()
    return out


def _memory(ctx: Any) -> tuple:
    db = stores.memory_db(ctx.project_root)
    return _stat(db), _stat(db.with_name(db.name + "-wal"))


def _tasks(ctx: Any) -> tuple:
    root = ctx.project_root / ".agentry" / "tasks"
    return tuple(_listing(root / name) for name in ("backlog", "active", "done", "epics"))


def _handoffs(ctx: Any) -> tuple:
    root = ctx.project_root / ".agentry"
    return _listing(root / "tasks" / "handoffs"), _listing(root / "state" / "memory")


def _status(ctx: Any) -> tuple:
    root = ctx.project_root / ".agentry"
    return _listing(root / "state", _STATUS_FILE.match), _stat(root / "pipeline.json")


def _harness(ctx: Any) -> tuple:
    claude = ctx.project_root / ".claude"
    plugin = ctx.plugin_root
    return (
        _listing(claude / "agents"),
        _listing(claude / "rules"),
        _listing(claude, lambda name: name == "CLAUDE.md" or name.startswith("settings")),
        _skill_listing(claude / "skills"),
        _skill_listing(plugin / "skills"),
        _stat(plugin / "hooks" / "hooks.json"),
        _stat(stores.manifest_path(plugin)),
    )


def _codegraph_dbs(root: Path) -> list[Path]:
    """The index of the project root and of each repos{} path (FR-32)."""
    roots = [root]
    repos = stores.read_pipeline(root).get("repos")
    if isinstance(repos, dict):
        roots += [root / s["path"] for s in repos.values()
                  if isinstance(s, dict) and isinstance(s.get("path"), str) and s["path"]]
    return [base / ".codegraph" / "codegraph.db" for base in roots]


def _codegraph(ctx: Any) -> tuple:
    return tuple(_stat(path) for path in _codegraph_dbs(ctx.project_root))


# Order is the order topics appear in a combined invalidate (FR-52).
SOURCES: tuple[tuple[str, tuple[str, ...], Callable[[Any], Any]], ...] = (
    ("runs", ("runs", "status"), _runs),
    ("memory", ("memory",), _memory),
    ("tasks", ("tasks",), _tasks),
    ("handoffs", ("handoffs", "status"), _handoffs),
    ("status", ("status", "runs"), _status),
    ("harness", ("harness",), _harness),
    ("codegraph", ("codegraph",), _codegraph),
)


# ---------------------------------------------------------------- events
def _event(row: tuple) -> dict:
    event = dict(zip(_EVENT_KEYS, row[:10]))
    try:
        detail = json.loads(row[10] or "{}")
    except ValueError:
        detail = {}
    event["detail"] = {k: detail[k] for k in _DETAIL_KEYS if isinstance(detail, dict) and k in detail}
    return event


def _events(ctx: Any, state: _State) -> list[dict]:
    """Messages for the rows past the cursor; moves the cursor. A locked or
    unreadable journal raises and leaves the cursor where it was."""
    with stores.store(stores.events_db(ctx.project_root)) as con:
        if con is None:
            return []
        latest = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        if latest < state.cursor:
            state.cursor = latest
            return [ws.message("resync", {"reason": "cursor_regression"})]
        if latest == state.cursor:
            return []
        rows = con.execute(
            "SELECT id, at, lane, task, pipeline, kind, stage, from_stage, outcome, via, detail "
            "FROM events WHERE id > ? ORDER BY id LIMIT ?", (state.cursor, EVENTS_BATCH + 1)).fetchall()
    if len(rows) > EVENTS_BATCH:
        state.cursor = latest
        return [ws.message("resync", {"reason": "burst"})]
    state.cursor = rows[-1][0]
    return [ws.message("events.appended", {"events": [_event(tuple(r)) for r in rows]})]


# ---------------------------------------------------------------- ticks
def _read(source: Callable[[Any], Any], ctx: Any) -> Any:
    try:
        return source(ctx)
    except (sqlite3.Error, OSError, ValueError):
        return _UNREAD


def _baseline(ctx: Any) -> _State:
    return _State(ws.latest_event_id(ctx), {name: _read(fn, ctx) for name, _, fn in SOURCES})


def _diff(ctx: Any, state: _State) -> list[dict]:
    messages: list[dict] = []
    try:
        messages += _events(ctx, state)
    except (sqlite3.Error, OSError, ValueError):
        pass  # locked or unreadable: no message this tick, the cursor stays (FR-50)
    topics: list[str] = []
    for name, source_topics, fn in SOURCES:
        current = _read(fn, ctx)
        if current is _UNREAD:
            continue
        previous = state.prints.get(name, _UNREAD)
        state.prints[name] = current
        if previous is not _UNREAD and previous != current:
            topics += [t for t in source_topics if t not in topics]
    if topics:
        messages.append(ws.message("invalidate", {"topics": topics}))
    return messages


def prime(ctx: Any) -> None:
    """Take the baseline now. ws calls this before it sends hello, so a change
    made after hello is always newer than the baseline."""
    global _state
    with _lock:
        if _state is None:
            _state = _baseline(ctx)


def tick(ctx: Any) -> None:
    """One watcher pass. With no client it forgets its baseline and does nothing else."""
    global _state
    with _lock:
        if ws.client_count() == 0:
            _state = None
            return
        if _state is None:
            _state = _baseline(ctx)
            return
        messages = _diff(ctx, _state)
    for msg in messages:
        ws.broadcast(msg)


def _loop(ctx: Any) -> None:
    while not _stop.wait(TICK_SECONDS):
        try:
            tick(ctx)
        except Exception:  # nothing a tick does may stop the watcher or reach the server
            pass


def start(ctx: Any) -> None:
    """Start the watcher thread (idempotent)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    ws.add_prime_hook(prime)
    _thread = threading.Thread(target=_loop, args=(ctx,), name="dashboard-watch", daemon=True)
    _thread.start()


def stop() -> None:
    global _thread, _state
    _stop.set()
    if _thread is not None:
        _thread.join(5.0)
    _thread = None
    with _lock:
        _state = None

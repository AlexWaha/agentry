"""Sandbox projects and a running dashboard server for the dashboard test modules.

Contract C2 of the task-0149 board:

    build_project(root, size="small") -> Path
    fixture_dist(dest) -> Path
    start_server(project, args=()) -> RunningServer   (.url .token .port .get() .stop())
    hash_tree(project) -> dict[str, str]

A sandbox project is built with the REAL harness writers, not with hand-made SQL: the
seed step runs this very file as a child process with CLAUDE_PROJECT_DIR (and
PIPELINE_LANE) pointing at the sandbox, so `state.create_run`, `memory.record_lesson`
and the events schema write exactly what a live session would. A child, not an
in-process patch: the harness modules bind their paths at import and the test process
has already imported them for the repository checkout.

Import this module from a test with the tests folder on sys.path (every test module
already does that for tmproot). It imports no harness module at import time.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
DASHBOARD_DIR = TOOLS_DIR / "dashboard"
SERVER = DASHBOARD_DIR / "server.py"
REPO_ROOT = TOOLS_DIR.parents[1]

LAUNCH_RE = re.compile(r"^http://127\.0\.0\.1:(\d+)/#token=([A-Za-z0-9_-]+)$")
START_TIMEOUT = 30.0

# --------------------------------------------------------------------------- the small dataset
# Tests assert against these constants instead of re-deriving them from the files.

SMALL_TASKS = {
    "backlog": [
        {"id": "task-0101", "title": "Alpha feature", "type": "feature", "epic": "epic-0001"},
        {"id": "task-0102", "title": "Beta bugfix", "type": "bugfix", "epic": "epic-0002"},
        {"id": "task-0103", "title": "Gamma superseded", "type": "feature", "epic": "epic-0001",
         "superseded_by": "task-0104"},
    ],
    "active": [
        {"id": "task-0104", "title": "Delta in flight", "type": "feature", "epic": "epic-0001",
         "priority": "high", "depends_on": ["task-0106"]},
        {"id": "task-0105", "title": "Epsilon awaiting commit", "type": "techdebt", "epic": ""},
    ],
    "done": [
        {"id": "task-0106", "title": "Zeta done", "type": "feature", "epic": "epic-0001"},
        {"id": "task-0107", "title": "Eta done", "type": "bugfix", "epic": "epic-0002"},
        {"id": "task-0108", "title": "Theta done", "type": "feature", "epic": ""},
        {"id": "task-0109", "title": "Iota done", "type": "refactor", "epic": "epic-0002"},
    ],
}
EVENT_BASE = "2026-10-10T08:00:00Z"
CREATED = "2026-10-01"


# --------------------------------------------------------------------------- project skeleton

def _json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2, sort_keys=False) + "\n").encode("utf-8"))


def make_project(root: Path, lanes: dict | None = None) -> Path:
    """The folders of an Agentry project and a pipeline.json derived from the repository's
    own, with the handoff and memory gates on and every done task covered."""
    root = Path(root)
    for sub in (".agentry/state", ".agentry/memory", ".agentry/tasks/backlog",
                ".agentry/tasks/active", ".agentry/tasks/done", ".agentry/tasks/epics",
                ".agentry/tasks/handoffs", ".agentry/plans"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = json.loads((REPO_ROOT / ".agentry" / "pipeline.json").read_text(encoding="utf-8"))
    config["lanes"] = lanes if lanes is not None else {"default": {"repo": "agentry"}}
    config["handoff"]["baseline"] = ""
    config["memory"]["baseline"] = ""
    _json(root / ".agentry" / "pipeline.json", config)
    return root


def write_task(root: Path, folder: str, task_id: str, *, title: str = "A task",
               type: str = "feature", priority: str = "medium", epic: str = "",
               spec: str = "none", assignee: str = "senior-backend-dev",
               created: str = CREATED, completed: str = "", depends_on=(),
               superseded_by: str = "", blocked_on: str = "", umbrella: bool = False,
               subtasks=(), phase: str = "harness", repo: str = "agentry",
               body: str = "## Description\n\nA task.\n") -> Path:
    """One task file. `body` is written byte for byte (CRLF and non-ASCII survive)."""
    lines = [f'id: "{task_id[5:]}"', f"type: {type}", f"priority: {priority}",
             f"title: {title}", f"phase: {phase}", f"repo: {repo}",
             f"depends_on: [{', '.join(depends_on)}]", f"blocked_on: {blocked_on}",
             f"superseded_by: {superseded_by}", f"epic: {epic}", f"spec: {spec}",
             f"created: {created}", f"completed: {completed}", f"assignee: {assignee}"]
    if umbrella:
        lines.append("umbrella: true")
        lines.append(f"subtasks: [{', '.join(subtasks)}]")
    head = "---\n" + "\n".join(lines) + "\n---\n"
    path = root / ".agentry" / "tasks" / folder / f"{task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head.encode("utf-8") + body.encode("utf-8"))
    return path


def write_epic(root: Path, name: str, title: str = "An epic", status: str = "planned",
               tasks=()) -> Path:
    path = root / ".agentry" / "tasks" / "epics" / f"{name}.md"
    path.write_bytes((f"---\nid: {name[5:9]}\ntitle: {title}\nstatus: {status}\n"
                      f"tasks: [{', '.join(tasks)}]\n---\n\n# {title}\n").encode())
    return path


_SECTION = ("This section holds enough real words to pass the minimum length check that "
            "the handoff validator applies to every required heading of the document.")


def write_handoff(root: Path, task: str, *, valid: bool = True, date: str = "2026-10-14",
                  title: str = "A handoff", author: str = "senior-backend-dev",
                  body_extra: str = "") -> Path:
    """A handoff doc that passes handoff.validate_handoff (valid) or fails it by lacking
    one required section."""
    sections = ["What was done", "Key decisions", "Files touched", "Gotchas and lessons",
                "Impact on next tasks"]
    if not valid:
        sections.remove("Gotchas and lessons")
    head = f"---\ntask: {task}\ntitle: {title}\nauthor: {author}\ndate: {date}\nmerge_commit: abc1234\n---\n"
    parts = [f"{head}\n# Handoff: {task}\n"]
    parts += [f"## {name}\n{_SECTION} {name}.\n" for name in sections]
    parts.append("## Context loaded\n- .agentry/project/project-context.md - the product context.\n"
                 "- spec for my upcoming task (none) - there is no spec for it.\n")
    if body_extra:
        parts.append(body_extra)
    path = root / ".agentry" / "tasks" / "handoffs" / f"{task}.md"
    path.write_bytes("\n".join(parts).encode("utf-8"))
    return path


def write_stamp(root: Path, task: str, at: str = "2026-10-15T09:30:00+00:00") -> Path:
    path = root / ".agentry" / "state" / "memory" / f"{task}.json"
    _json(path, {"task": task, "stamped_at": at, "counts": {}, "none": True})
    return path


# --------------------------------------------------------------------------- seed (real writers)

def seed(root: Path, spec: dict, lane: str = "") -> None:
    """Run the real harness writers in a child process rooted at the sandbox.

    spec keys, all optional:
      runs        [{task, type, stage, pipeline, fields: {column: value}}]  state.create_run + set_fields
      events      [{kind, task, at, lane, pipeline, stage, from_stage, outcome, via, detail}]
      bulk_events N          grow the journal to N events in all, over 300 tasks (kinds cycle)
      lessons / patterns / modules   [kwargs of memory.record_*]
      retire      [{signature, reason, agent}]
      bulk_lessons N         grow the lessons table to N rows in all (every 7th says "needleword")
      supervisor  {task: {last_label, seen}}  supervisor.save_registry
      dials       {mode, approvals, swarm}
    `lane` is the PIPELINE_LANE of the child ("" for the default lane)."""
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env.pop("PIPELINE_LANE", None)
    if lane and lane != "default":
        env["PIPELINE_LANE"] = lane
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--seed"],
                          input=json.dumps(spec).encode("utf-8"), env=env,
                          capture_output=True, timeout=300)
    if done.returncode != 0:
        raise RuntimeError("sandbox seed failed:\n" + done.stderr.decode("utf-8", "replace"))


def _seed_child() -> int:
    spec = json.loads(sys.stdin.read())
    for sub in ("memory", "pipeline", ""):
        sys.path.insert(0, str(TOOLS_DIR / sub) if sub else str(TOOLS_DIR))
    import events
    import memory
    import state

    if spec.get("runs"):
        conn = state.connect()
        try:
            for run in spec["runs"]:
                state.create_run(conn, run["task"], run.get("type", "feature"),
                                 run.get("stage", "implement"), run.get("pipeline", "build"))
                if run.get("fields"):
                    state.set_fields(conn, run["task"], **run["fields"])
        finally:
            conn.close()

    rows = list(spec.get("events", []))
    kinds = ["registered", "transition", "gate", "gate", "gate", "rejected", "approved", "merged"]
    if rows or spec.get("bulk_events"):
        conn = events._connect()
        try:
            existing = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            for i in range(max(0, int(spec.get("bulk_events", 0)) - existing - len(rows))):
                kind = kinds[i % len(kinds)]
                rows.append({"kind": kind, "task": f"task-{i % 300 + 1:04d}",
                             "at": "2026-09-01T00:00:00Z", "stage": ("implement", "test", "review")[i % 3],
                             "outcome": ("pass", "fail")[i % 5 == 0] if kind == "gate" else "",
                             "detail": {"duration_ms": 1000 + i % 977, "reason": f"reason {i}"}})
            conn.execute("BEGIN")
            conn.executemany(events._INSERT, [
                (e.get("at") or "2026-10-10T08:00:00Z", e.get("lane", ""), e["task"],
                 e.get("pipeline", ""), e["kind"], e.get("stage", ""), e.get("from_stage", ""),
                 e.get("outcome", ""), e.get("via", ""),
                 json.dumps(e.get("detail", {}), sort_keys=True)) for e in rows])
            conn.execute("COMMIT")
        finally:
            conn.close()

    if any(spec.get(k) for k in ("lessons", "patterns", "modules", "retire", "bulk_lessons")):
        conn = memory.connect()
        try:
            conn.execute("PRAGMA synchronous = OFF")
            for lesson in spec.get("lessons", []):
                memory.record_lesson(conn, **lesson)
            existing = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
            for i in range(max(0, int(spec.get("bulk_lessons", 0)) - existing)):
                memory.record_lesson(
                    conn, signature=f"bulk-lesson-{i:05d}", trigger=f"trigger number {i}",
                    what=f"what happened in case {i}" + (" needleword" if i % 7 == 0 else ""),
                    why="a root cause", fix="a fix", area=f"area-{i % 9}",
                    task=f"task-{i % 300 + 1:04d}", agent=("senior-backend-dev", "qa-engineer")[i % 2],
                    created=f"2026-09-{i % 28 + 1:02d}")
            for pattern in spec.get("patterns", []):
                memory.record_pattern(conn, **pattern)
            for module in spec.get("modules", []):
                memory.record_module(conn, **module)
            for retire in spec.get("retire", []):
                memory.retire_lesson(conn, **retire)
        finally:
            conn.close()

    if spec.get("supervisor"):
        import supervisor
        supervisor.save_registry({"tasks": spec["supervisor"], "actions": [], "debt": []})
    dials = spec.get("dials") or {}
    if dials:
        import approvals
        import mode
        import swarm
        if "mode" in dials:
            mode.write(dials["mode"])
        if "approvals" in dials:
            approvals.write(dials["approvals"])
        if "swarm" in dials:
            swarm.write(dials["swarm"])
    return 0


# --------------------------------------------------------------------------- build_project

def _small_events() -> list[dict]:
    """A hand-countable journal: task-0106 goes through the pipeline and merges, task-0104
    is mid-flight with one failed gate, task-0107 is rejected once."""
    def ev(minutes: int, kind: str, task: str, **fields) -> dict:
        base = int(EVENT_BASE[11:13]) * 60 + int(EVENT_BASE[14:16]) + minutes
        day = 10 + base // 1440
        at = f"2026-10-{day:02d}T{base % 1440 // 60:02d}:{base % 60:02d}:00Z"
        return {"kind": kind, "task": task, "at": at, "pipeline": "build", **fields}

    return [
        ev(0, "registered", "task-0106", stage="plan", detail={"type": "feature"}),
        ev(10, "transition", "task-0106", stage="implement", from_stage="plan"),
        ev(20, "gate", "task-0106", stage="implement", outcome="pass",
           detail={"cmd": "compileall", "exit": 0, "duration_ms": 2000}),
        ev(30, "transition", "task-0106", stage="test", from_stage="implement"),
        ev(40, "gate", "task-0106", stage="test", outcome="fail",
           detail={"cmd": "unittest", "exit": 1, "duration_ms": 6000}),
        ev(50, "gate", "task-0106", stage="test", outcome="pass",
           detail={"cmd": "unittest", "exit": 0, "duration_ms": 4000}),
        ev(60, "merged", "task-0106", stage="done"),
        ev(100, "registered", "task-0104", stage="implement", detail={"type": "feature"}),
        ev(110, "gate", "task-0104", stage="implement", outcome="fail",
           detail={"cmd": "compileall", "exit": 1, "duration_ms": 1000}),
        ev(120, "rejected", "task-0107", stage="implement", from_stage="ready",
           detail={"reason": "needs a test"}),
        ev(130, "parked", "task-0105", stage="ready", outcome="commit"),
    ]


def build_project(root: Path, size: str = "small") -> Path:
    """Populate `root` as an Agentry project. size "small" is the hand-countable dataset
    above; "synthetic" is the NFR-14 volume: 10000 events, 5000 memory rows, 1000 task
    files and 500 handoff files."""
    if size not in ("small", "synthetic"):
        raise ValueError(f"unknown size {size!r}")
    root = make_project(root, {"default": {"repo": "agentry"}, "ghost": {"repo": "agentry"}})
    for folder, tasks in SMALL_TASKS.items():
        for task in tasks:
            fields = {k: v for k, v in task.items() if k != "id"}
            write_task(root, folder, task["id"], **fields)
    write_epic(root, "epic-0001-first", "First epic", tasks=["task-0101", "task-0103", "task-0104",
                                                           "task-0106"])
    write_epic(root, "epic-0001-thread", "Thread, not an epic")
    write_epic(root, "epic-0002-second", "Second epic", status="active",
               tasks=["task-0102", "task-0107", "task-0109"])
    write_handoff(root, "task-0106", valid=True)
    write_handoff(root, "task-0107", valid=False, date="2026-10-15")
    write_stamp(root, "task-0106")
    (root / ".agentry" / "state" / "run.db.bak-1").write_bytes(b"not a database")

    seed(root, {
        "runs": [
            {"task": "task-0104", "stage": "implement"},
            {"task": "task-0105", "type": "techdebt", "stage": "ready",
             "fields": {"awaiting_human": "commit"}},
            {"task": "task-0106", "stage": "done"},
            {"task": "task-0109", "type": "refactor", "stage": "done",
             "fields": {"stage_status": "abandoned", "abandon_reason": "dropped by the User"}},
            {"task": "task-0108", "stage": "test", "fields": {"stage_status": "paused",
                                                              "pause_reason": "force majeure"}},
        ],
        "events": _small_events(),
        "lessons": [
            {"signature": "alpha-lesson", "trigger": "when alpha", "what": "alpha went wrong",
             "why": "alpha cause", "fix": "alpha fix", "area": "hooks", "task": "task-0106",
             "agent": "senior-backend-dev", "created": "2026-10-11"},
            {"signature": "beta-lesson", "trigger": "when beta", "what": "beta went wrong",
             "why": "beta cause", "fix": "beta fix", "area": "hooks", "task": "task-0107",
             "agent": "qa-engineer", "created": "2026-10-12"},
            {"signature": "gamma-lesson", "trigger": "when gamma", "what": "gamma retired",
             "why": "gamma cause", "fix": "gamma fix", "area": "docs", "task": "",
             "agent": "", "created": "2026-10-09"},
        ],
        "patterns": [{"name": "pattern-one", "use_when": "you need one", "body": "do one",
                      "task": "task-0106", "created": "2026-10-11"},
                     {"name": "pattern-two", "use_when": "you need two", "body": "",
                      "task": "", "created": "2026-10-12"}],
        "modules": [{"path": "plugin/tools/pipeline/state.py", "responsibility": "run state",
                     "symbols": "connect create_run", "notes": ""},
                    {"path": "plugin/tools/memory/memory.py", "responsibility": "memory store"}],
        "retire": [{"signature": "gamma-lesson", "reason": "withdrawn in the sandbox"}],
        "dials": {"mode": "build", "approvals": "manual", "swarm": "off"},
    })
    seed(root, {"runs": [{"task": "task-0102", "type": "bugfix", "stage": "implement",
                          "fields": {"stage_status": "blocked"}}]}, lane="fix")
    seed(root, {"supervisor": {"task-0104": {"last_label": "HEALTHY", "seen": 1792000000.0}}})
    if size == "synthetic":
        _grow_synthetic(root)
    return root


def _grow_synthetic(root: Path) -> None:
    for i in range(200, 1191):  # with the nine small tasks: exactly 1000 task files
        folder = ("backlog", "active", "done")[i % 3]
        write_task(root, folder, f"task-{i:04d}", title=f"Synthetic task {i} word{i % 11}",
                   type=("feature", "bugfix", "techdebt")[i % 3], epic=f"epic-00{i % 4:02d}",
                   body="## Description\n\nSynthetic.\n")
    for i in range(500):
        write_handoff(root, f"task-{i + 1:04d}", valid=i % 5 != 0,
                      date=f"2026-{i % 9 + 1:02d}-{i % 27 + 1:02d}")
    seed(root, {"bulk_events": 10000})
    seed(root, {"bulk_lessons": 4996})  # with 3 small lessons, 2 patterns and 2 modules: 5000 rows


# --------------------------------------------------------------------------- codegraph

def make_codegraph(parent: Path, files: list[str], edges: list[tuple[str, str, int]],
                   symbols_per_file: int = 2) -> Path:
    """`<parent>/.codegraph/codegraph.db` with the tables the dashboard probes (nodes, edges,
    files). `edges` is (from file, to file, how many edges between their symbols)."""
    import sqlite3
    folder = Path(parent) / ".codegraph"
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "codegraph.db"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
            CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                qualified_name TEXT NOT NULL, file_path TEXT NOT NULL, language TEXT NOT NULL);
            CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                target TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                language TEXT NOT NULL, size INTEGER NOT NULL);
        """)
        for path in files:
            conn.execute("INSERT INTO files VALUES (?, 'h', 'python', 10)", (path,))
            for n in range(symbols_per_file):
                conn.execute("INSERT INTO nodes VALUES (?, 'function', ?, ?, ?, 'python')",
                             (f"{path}#{n}", f"fn{n}", f"{path}::fn{n}", path))
        for source, target, count in edges:
            for n in range(count):
                low, high = n % symbols_per_file, n // symbols_per_file % symbols_per_file
                conn.execute("INSERT INTO edges (source, target, kind) VALUES (?, ?, 'calls')",
                             (f"{source}#{low}", f"{target}#{high}"))
        conn.commit()
    finally:
        conn.close()
    return db


# --------------------------------------------------------------------------- fixture dist

INDEX_HTML = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
              '<title>Agentry fixture</title><link rel="icon" href="/favicon.svg">'
              '<script src="/assets/app-abc123.js"></script>'
              '<link rel="stylesheet" href="/assets/app-abc123.css"></head>'
              '<body><div id="root"></div></body></html>')


def fixture_dist(dest: Path) -> Path:
    """A valid tiny dist with a manifest, one file per type of the server's MIME map."""
    dest = Path(dest)
    (dest / "assets").mkdir(parents=True, exist_ok=True)
    files = {
        "index.html": INDEX_HTML.encode("utf-8"),
        "favicon.svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
        "favicon.ico": b"\x00\x00\x01\x00\x00\x00",
        "assets/app-abc123.js": b"console.log('fixture');\n",
        "assets/app-abc123.css": b"body{margin:0}\n",
        "assets/data.json": b'{"fixture": true}\n',
        "assets/pic.png": b"\x89PNG\r\n\x1a\n",
        "assets/font.woff2": b"wOF2",
        "assets/note.txt": b"fixture text\n",
    }
    for rel, content in files.items():
        (dest / rel).write_bytes(content)
    sys.path.insert(0, str(DASHBOARD_DIR))
    import dist_manifest
    dist_manifest.write(dest, None)
    return dest


# --------------------------------------------------------------------------- hashing

def hash_tree(project: Path) -> dict[str, str]:
    """Relative path -> sha256 for every file under `project`, directories as "<dir>".
    SQLite `-wal` and `-shm` sidecars are excluded: a read-only reader of a WAL store
    may create them (spec-0002 FR-9)."""
    project = Path(project)
    out: dict[str, str] = {}
    for current, dirs, names in os.walk(project):
        for name in dirs:
            out[(Path(current) / name).relative_to(project).as_posix()] = "<dir>"
        for name in names:
            if name.endswith(("-wal", "-shm")):
                continue
            path = Path(current) / name
            out[path.relative_to(project).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------------- server

class RunningServer:
    """A dashboard server child process. `get` carries the right token, Host and Origin by
    default; pass `headers={"Authorization": None}` to drop a header."""

    def __init__(self, proc: subprocess.Popen, url: str, token: str, port: int,
                 stdout_lines: list[str], stderr_lines: list[str], cleanup: list) -> None:
        self.proc = proc
        self.url = url
        self.token = token
        self.port = port
        self.stdout_lines = stdout_lines
        self.stderr_lines = stderr_lines
        self._cleanup = cleanup

    def request(self, method: str, path: str, headers: dict | None = None, body: bytes | None = None,
                ) -> tuple[int, http.client.HTTPMessage, bytes]:
        merged = {"Authorization": f"Bearer {self.token}"}
        merged.update(headers or {})
        merged = {k: v for k, v in merged.items() if v is not None}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=merged)
            response = conn.getresponse()
            return response.status, response.msg, response.read()
        finally:
            conn.close()

    def get(self, path: str, headers: dict | None = None) -> tuple[int, http.client.HTTPMessage, bytes]:
        return self.request("GET", path, headers)

    def get_json(self, path: str, headers: dict | None = None):
        status, head, body = self.get(path, headers)
        return status, head, json.loads(body.decode("utf-8")) if body else None

    def stderr_text(self) -> str:
        return "".join(self.stderr_lines)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 10.0) -> int:
        """Stop the child the way the OS stop signal does and return its exit code."""
        if self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.terminate()
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
            except OSError:
                pass
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()
        for action in self._cleanup:
            action()
        self._cleanup = []
        return self.proc.returncode

    def __enter__(self) -> RunningServer:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _drain(stream, lines: list[str], first: queue.Queue | None) -> None:
    for line in iter(stream.readline, ""):
        lines.append(line)
        if first is not None and first.empty():
            first.put(line)


def spawn_server(project: Path, args: tuple[str, ...] = (), env_extra: dict | None = None,
                 ) -> subprocess.Popen:
    env = os.environ.copy()
    env.pop("PIPELINE_LANE", None)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen([sys.executable, str(SERVER), *args], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", creationflags=flags)


def start_server(project: Path, args: tuple[str, ...] = (), dist: Path | None = None,
                 env_extra: dict | None = None) -> RunningServer:
    """Start server.py as a child on `project`, wait for its launch line, return a handle.

    A fixture dist is built next to the logs unless `dist` or a --dist-dir argument is
    given. The caller (or `with`) must stop() it."""
    import tmproot
    cleanup = []
    args = tuple(args)
    if "--dist-dir" not in args:
        scratch = tmproot.mkdtemp("dash_dist_")
        cleanup.append(lambda: tmproot.rmtree(scratch))
        args = ("--dist-dir", str(dist or fixture_dist(scratch / "dist")), *args)
    if "--no-open" not in args:
        args = ("--no-open", *args)
    proc = spawn_server(project, args, env_extra)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    first: queue.Queue = queue.Queue()
    threading.Thread(target=_drain, args=(proc.stdout, stdout_lines, first), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, stderr_lines, None), daemon=True).start()
    try:
        line = first.get(timeout=START_TIMEOUT)
    except queue.Empty:
        proc.kill()
        raise RuntimeError("dashboard server printed no launch line:\n" + "".join(stderr_lines)) from None
    match = LAUNCH_RE.match(line.strip())
    if not match:
        proc.kill()
        raise RuntimeError(f"unexpected first stdout line {line!r}:\n" + "".join(stderr_lines))
    server = RunningServer(proc, line.strip(), match.group(2), int(match.group(1)),
                           stdout_lines, stderr_lines, cleanup)
    time.sleep(0.05)
    return server


if __name__ == "__main__":
    if sys.argv[1:] == ["--seed"]:
        raise SystemExit(_seed_child())
    sys.stderr.write("dashboard_sandbox.py is a test helper; run with --seed to seed a project\n")
    raise SystemExit(2)

"""Memory, knowledge-graph and codegraph read model (spec-0002 FR-28 to FR-33, FR-123).

The memory rows are served in the shape of the memory.db columns (see the board decision
of 0149-core): Lesson, Pattern and Module differ, and each carries exactly its own columns.
Nothing here writes: memory.db and codegraph.db are opened through stores.store (mode=ro).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import stores

stores.ensure_tool_paths()

import inject  # noqa: E402  (harness modules: the tool folders were put on sys.path above)
import memory  # noqa: E402

GROWTH_DAYS = 30
DEFAULT_BUDGET_BYTES = 3800
QUERY_BUDGET_S = 3.0
GRAPH_CACHE_MAX = 32
GRAPH_LEVELS = ("module", "file")
RETIRED_MODES = ("exclude", "include", "only")
TEXT_COLUMNS = {
    "lesson": ("signature", "trigger", "what", "why", "fix", "area", "task", "agent"),
    "pattern": ("name", "use_when", "body", "task"),
    "module": ("path", "responsibility", "symbols", "notes"),
}
ORDER = {"lesson": "t.created DESC, t.id DESC", "pattern": "t.created DESC, t.id DESC",
         "module": "t.updated DESC, t.id DESC"}
CODEGRAPH_REQUIRED = {"nodes": {"id", "file_path"}, "edges": {"source", "target"}, "files": {"path"}}
INJECT_LINE = re.compile(r"[\\/]inject\.py(?=[\"\s]|$)")

_graph_cache: dict[tuple, tuple[dict, dict]] = {}
_graph_lock = threading.Lock()


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# --------------------------------------------------------------------------- rows

def _row_out(kind: str, row: sqlite3.Row) -> dict:
    have = set(row.keys())

    def s(name: str) -> str:
        return str(row[name]) if name in have and row[name] is not None else ""

    if kind == "lesson":
        return {"kind": "lesson", "id": row["id"], "signature": s("signature"), "trigger": s("trigger"),
                "what": s("what"), "why": s("why"), "fix": s("fix"), "area": s("area"),
                "task": s("task"), "agent": s("agent"), "retired": bool(s("retired")),
                "created": stores.iso_z(s("created"))}
    if kind == "pattern":
        return {"kind": "pattern", "id": row["id"], "name": s("name"), "use_when": s("use_when"),
                "body": s("body"), "task": s("task"), "created": stores.iso_z(s("created"))}
    return {"kind": "module", "id": row["id"], "path": s("path"),
            "responsibility": s("responsibility"), "symbols": s("symbols"), "notes": s("notes"),
            "updated": stores.iso_z(s("updated"))}


def _filters(kind: str, conn: sqlite3.Connection, agent: str, area: str, task: str,
             retired: str) -> tuple[list[str], list]:
    """WHERE fragments on table alias t. A filter on a column the kind does not have
    matches nothing, the same as an absent value would."""
    conds: list[str] = []
    args: list = []
    for name, value, kinds in (("agent", agent, ("lesson",)), ("area", area, ("lesson",)),
                               ("task", task, ("lesson", "pattern"))):
        if value:
            if kind in kinds:
                conds.append(f"t.{name} = ?")
                args.append(value)
            else:
                conds.append("0")
    if kind == "lesson" and "retired" in _cols(conn, "lessons"):
        if retired == "exclude":
            conds.append("t.retired = ''")
        elif retired == "only":
            conds.append("t.retired != ''")
    elif kind == "lesson" and retired == "only":
        conds.append("0")
    return conds, args


def memory_rows(ctx: stores.Ctx, params: stores.Params):
    kind = params.choice("kind", memory.KINDS, "lesson")
    text = params.text("q")
    agent, area, task = params.text("agent"), params.text("area"), params.text("task")
    retired = params.choice("retired", RETIRED_MODES, "exclude")
    limit, offset = params.page(20, 200)
    params.check()
    table = memory.TABLE[kind]
    rows: list[dict] = []
    total = 0
    with stores.store(stores.memory_db(ctx.project_root)) as conn:
        if conn is not None and stores.has_table(conn, table):
            conds, args = _filters(kind, conn, agent, area, task, retired)
            ranked = None
            if text and retired == "exclude":
                ranked = _ranked(conn, kind, table, text, conds, args, limit, offset)
            if ranked is not None:
                total, found = ranked
            else:
                if text:
                    columns = TEXT_COLUMNS[kind]
                    conds.append("(" + " OR ".join(f"t.{c} LIKE ? ESCAPE '\\'" for c in columns) + ")")
                    args.extend([_like(text)] * len(columns))
                where = (" WHERE " + " AND ".join(conds)) if conds else ""
                total = conn.execute(f"SELECT COUNT(*) FROM {table} t{where}", args).fetchone()[0]
                found = conn.execute(f"SELECT t.* FROM {table} t{where} ORDER BY {ORDER[kind]} "
                                     f"LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            rows = [_row_out(kind, r) for r in found]
    return rows, {"total": total, "limit": limit, "offset": offset}


def _ranked(conn, kind: str, table: str, text: str, conds: list[str], args: list,
            limit: int, offset: int):
    """(exact total, one page of rows) ranked by the FTS5 index, or None when the query has
    no usable term or FTS5 cannot answer (the caller then falls back to LIKE)."""
    match = memory.fts_query(text)
    if not match:
        return None
    tail = "".join(f" AND {c}" for c in conds)
    base = (f"FROM mem_fts JOIN {table} t ON t.id = CAST(substr(mem_fts.ref, "
            f"instr(mem_fts.ref, ':') + 1) AS INTEGER) WHERE mem_fts MATCH ? "
            f"AND mem_fts.kind = ?{tail}")
    try:
        total = conn.execute(f"SELECT COUNT(*) {base}", (match, kind, *args)).fetchone()[0]
        found = conn.execute(f"SELECT t.* {base} ORDER BY mem_fts.rank, t.id LIMIT ? OFFSET ?",
                             (match, kind, *args, limit, offset)).fetchall()
    except sqlite3.OperationalError:
        return None
    return total, found


# --------------------------------------------------------------------------- stats

def _injection_budget(root: Path) -> int:
    block = stores.read_pipeline(root).get("memory")
    raw = block.get("inject_budget_bytes") if isinstance(block, dict) else None
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 else DEFAULT_BUDGET_BYTES


def _profiles(plugin_root: Path) -> list[tuple[str, list[str], int]]:
    """(matcher, kinds, limit) of every inject.py command under SubagentStart in hooks.json."""
    groups = stores.read_json(plugin_root / "hooks" / "hooks.json").get("hooks", {})
    groups = groups.get("SubagentStart", []) if isinstance(groups, dict) else []
    out = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []):
            command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
            if not INJECT_LINE.search(command):
                continue
            kinds = re.search(r"--kinds\s+(\S+)", command)
            limit = re.search(r"--limit\s+(\d+)", command)
            out.append((str(group.get("matcher") or ""),
                        [k for k in (kinds.group(1) if kinds else "lesson").split(",") if k],
                        int(limit.group(1)) if limit else 6))
    return out


def _injection(ctx: stores.Ctx, conn) -> dict:
    """What each SubagentStart profile would inject now, computed by the hook's own render."""
    root = ctx.project_root
    text = inject.active_task_text()
    terms = memory.fts_terms(text) if text.strip() else []
    active = sorted(p.stem for p in (stores.tasks_dir(root) / "active").glob("task-*.md"))
    profiles = []
    for matcher, kinds, limit in _profiles(ctx.plugin_root):
        hits = (memory.query(text, limit=max(1, limit), kinds=kinds, conn=conn)
                if conn is not None and text.strip() else [])
        block = inject.render(hits, terms) if hits else ""
        profiles.append({"matcher": matcher, "kinds": kinds, "limit": limit, "matched": len(hits),
                         "shown": sum(1 for line in block.splitlines() if line.startswith("- [")),
                         "bytes": len(block.encode("utf-8"))})
    return {"budget_bytes": _injection_budget(root), "active_tasks": active, "profiles": profiles}


def memory_stats(ctx: stores.Ctx, params: stores.Params):
    params.check()
    root = ctx.project_root
    path = stores.memory_db(root)
    wal = path.with_name(path.name + "-wal")
    counts = {kind: 0 for kind in memory.KINDS}
    by_agent: Counter = Counter()
    by_area: Counter = Counter()
    created: Counter = Counter()
    with stores.store(path) as conn:
        if conn is not None:
            for kind, table in memory.TABLE.items():
                if stores.has_table(conn, table):
                    counts[kind] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if counts["lesson"]:
                by_agent.update(dict(conn.execute("SELECT agent, COUNT(*) FROM lessons WHERE agent != '' "
                                                  "GROUP BY agent").fetchall()))
                by_area.update(dict(conn.execute("SELECT area, COUNT(*) FROM lessons WHERE area != '' "
                                                 "GROUP BY area").fetchall()))
            for table in ("lessons", "patterns"):
                if stores.has_table(conn, table):
                    created.update(dict(conn.execute(
                        f"SELECT substr(created, 1, 10), COUNT(*) FROM {table} GROUP BY 1").fetchall()))
        injection = _injection(ctx, conn)
    today = stores.utc_now().date()
    days = [(today - timedelta(days=GROWTH_DAYS - 1 - i)).isoformat() for i in range(GROWTH_DAYS)]
    stamps = []
    for stamp in (stores.state_dir(root) / "memory").glob("task-*.json"):
        stamps.append({"task": stamp.stem,
                       "at": stores.iso_z(stores.read_json(stamp).get("stamped_at"))})
    stamps.sort(key=lambda s: s["task"])
    stamps.sort(key=lambda s: s["at"], reverse=True)

    def ranked(counter: Counter) -> dict:
        return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

    return {
        "db_bytes": path.stat().st_size if path.is_file() else 0,
        "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
        "counts": counts,
        "growth": [{"day": day, "rows": created.get(day, 0)} for day in days],
        "by_agent": ranked(by_agent),
        "by_area": ranked(by_area),
        "stamps": stamps,
        "injection": injection,
    }, None


# --------------------------------------------------------------------------- memory graph

def memory_graph(ctx: stores.Ctx, params: stores.Params):
    params.check()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    degree: Counter = Counter()

    def link(source: str, kind: str, value: str) -> None:
        target = f"{kind}:{value}"
        nodes.setdefault(target, {"id": target, "kind": kind, "label": value, "weight": 0})
        edges.append({"source": source, "target": target, "kind": kind, "weight": 1})
        degree[source] += 1
        degree[target] += 1

    with stores.store(stores.memory_db(ctx.project_root)) as conn:
        if conn is not None:
            if stores.has_table(conn, "lessons"):
                retired = " WHERE retired = ''" if "retired" in _cols(conn, "lessons") else ""
                for row in conn.execute(f"SELECT id, signature, task, agent, area FROM lessons{retired} "
                                        f"ORDER BY id"):
                    node = f"lesson:{row['id']}"
                    nodes[node] = {"id": node, "kind": "lesson", "label": row["signature"], "weight": 0}
                    for kind in ("task", "agent", "area"):
                        if row[kind]:
                            link(node, kind, row[kind])
            if stores.has_table(conn, "patterns"):
                for row in conn.execute("SELECT id, name, task FROM patterns ORDER BY id"):
                    node = f"pattern:{row['id']}"
                    nodes[node] = {"id": node, "kind": "pattern", "label": row["name"], "weight": 0}
                    if row["task"]:
                        link(node, "task", row["task"])
    for node_id, node in nodes.items():
        node["weight"] = degree[node_id]
    return {"nodes": list(nodes.values()), "edges": edges}, None


# --------------------------------------------------------------------------- codegraph

def _index_dirs(ctx: stores.Ctx) -> dict[str, Path]:
    """key -> folder that would hold codegraph.db: `root` and each repos{} path."""
    root = ctx.project_root
    out = {"root": root / ".codegraph"}
    repos = stores.read_pipeline(root).get("repos")
    for key, entry in (repos.items() if isinstance(repos, dict) else []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and key not in out:
            out[str(key)] = (root / entry["path"]).resolve() / ".codegraph"
    return out


def _supported(conn: sqlite3.Connection) -> bool:
    try:
        return all(cols <= _cols(conn, table) for table, cols in CODEGRAPH_REQUIRED.items())
    except sqlite3.Error:
        return False


def _display(root: Path, folder: Path) -> str:
    try:
        return folder.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return folder.resolve().as_posix()


def codegraph(ctx: stores.Ctx, params: stores.Params):
    params.check()
    out, seen = [], set()
    for key, folder in _index_dirs(ctx).items():
        db = folder / "codegraph.db"
        if not db.is_file() or db.resolve() in seen:
            continue
        seen.add(db.resolve())
        entry = {"key": key, "path": _display(ctx.project_root, folder),
                 "db_bytes": db.stat().st_size, "last_sync": stores.epoch_iso(db.stat().st_mtime),
                 "supported": False, "files": 0, "symbols": 0, "edges": 0}
        try:
            with stores.store(db) as conn:
                if conn is not None and _supported(conn):
                    entry.update(supported=True,
                                 files=conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                                 symbols=conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                                 edges=conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        except sqlite3.Error:
            entry["supported"] = False
        out.append(entry)
    return out, None


def _group(path: str, level: str) -> str:
    path = (path or "").replace("\\", "/")
    if level == "file":
        return path
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _signature(db: Path) -> tuple:
    def stat(p: Path) -> tuple:
        try:
            s = p.stat()
            return s.st_mtime_ns, s.st_size
        except OSError:
            return (0, 0)
    return stat(db), stat(db.with_name(db.name + "-wal"))


def _build_graph(db: Path, level: str, limit: int) -> tuple[dict, dict]:
    started = time.monotonic()
    deadline = started + QUERY_BUDGET_S
    truncated = False
    weights: Counter = Counter()
    with stores.store(db) as conn:
        if conn is not None and _supported(conn):
            conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 2000)
            try:
                cursor = conn.execute("SELECT ns.file_path, nt.file_path FROM edges e "
                                      "JOIN nodes ns ON ns.id = e.source JOIN nodes nt ON nt.id = e.target")
                for i, row in enumerate(cursor):
                    if i % 1024 == 0 and time.monotonic() >= deadline:
                        truncated = True
                        break
                    a, b = _group(row[0], level), _group(row[1], level)
                    if a and b and a != b:
                        weights[(a, b)] += 1
            except sqlite3.OperationalError as exc:
                if "interrupt" not in str(exc).lower():
                    raise
                truncated = True
    neighbours: dict[str, set] = defaultdict(set)
    for a, b in weights:
        neighbours[a].add(b)
        neighbours[b].add(a)
    kept = sorted(neighbours, key=lambda n: (-len(neighbours[n]), n))[:limit]
    keep = set(kept)
    graph = {
        "nodes": [{"id": n, "kind": level, "label": n, "weight": len(neighbours[n])} for n in kept],
        "edges": [{"source": a, "target": b, "kind": "depends", "weight": w}
                  for (a, b), w in sorted(weights.items()) if a in keep and b in keep],
    }
    return graph, {"truncated": truncated, "elapsed_ms": int((time.monotonic() - started) * 1000)}


def codegraph_graph(ctx: stores.Ctx, params: stores.Params, key: str):
    level = params.choice("level", GRAPH_LEVELS, "module")
    limit = params.integer("limit", 150, 10, 400)
    dirs = _index_dirs(ctx)
    if key not in dirs:
        params.reject("key", "must be root or a key of repos in pipeline.json")
    params.check()
    db = dirs[key] / "codegraph.db"
    if not db.is_file():
        raise stores.not_found("No codegraph index for this key")
    cache_key = (str(db.resolve()), level, limit, _signature(db))
    with _graph_lock:
        hit = _graph_cache.get(cache_key)
    if hit is None:
        hit = _build_graph(db, level, limit)
        with _graph_lock:
            if len(_graph_cache) >= GRAPH_CACHE_MAX:
                _graph_cache.clear()
            _graph_cache[cache_key] = hit
    return hit

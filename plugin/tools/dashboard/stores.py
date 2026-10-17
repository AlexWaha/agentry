"""Read-only data access for the dashboard server (spec-0002 FR-1 to FR-3, FR-10, FR-15).

Everything the views read from disk goes through this module:

  * ro_connect is the ONLY sqlite3.connect in plugin/tools/dashboard/ (FR-1). It opens
    the store `mode=ro`, returns None for a missing file and creates nothing (FR-2). A
    connection never outlives the request that opened it (FR-3): use store() so the
    close sits in a finally block.
  * lane discovery, task / epic parsing with a stat-keyed cache, the paging and query
    parameter helpers, the error type the server turns into the JSON error body.

No harness module is imported here; the views do that. The module is stdlib only and
works without CLAUDE_PROJECT_DIR, which keeps `server.py --help` independent of it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS_DIR = HERE.parent

FOLDERS = ("backlog", "active", "done")
DEFAULT_LANE = "default"
LANE_DB_RE = re.compile(r"^run(\.[A-Za-z0-9_-]{1,32})?\.db$")
LANE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
TASK_ID_RE = re.compile(r"^task-[0-9]{4,}$")
TASK_FILE_RE = re.compile(r"^(task-[0-9]{4,})\.md$")
TASK_REF_RE = re.compile(r"task-[0-9]+")

# One request or response never carries more than this much of a task document.
BODY_CAP_BYTES = 262144
# A task or epic file is read at most this far; frontmatter and the capped body fit.
READ_CAP_BYTES = 1 << 20
# Frontmatter is read and parsed at most this far, wherever it comes from.
HEAD_CAP_BYTES = 16384
# A dial file (`mode`, `approvals`, `swarm`) holds one word.
DIAL_CAP_BYTES = 64
MAX_OFFSET = 10 ** 9
BUSY_TIMEOUT_MS = 500
READ_WORKERS = 16
PARALLEL_FROM = 32


@dataclass(frozen=True)
class Ctx:
    """What a request or the watcher needs to know about this server instance."""

    project_root: Path
    plugin_root: Path
    port: int
    origin: str
    token: str
    dist_dir: Path


def ensure_tool_paths() -> None:
    """Put the harness tool folders on sys.path (the tools are scripts, not a package).

    Final order: dashboard, memory, pipeline, hooks, tools. The inserts are LIFO and the
    memory folder lands before pipeline for the reason inject.py and update.py give:
    a sibling module named like a folder must not shadow the module in it."""
    for sub in ("", "hooks", "pipeline", "memory"):
        folder = str(TOOLS_DIR / sub) if sub else str(TOOLS_DIR)
        if folder not in sys.path:
            sys.path.insert(0, folder)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))


# --------------------------------------------------------------------------- stores

def ro_connect(path: Path) -> sqlite3.Connection | None:
    """Open an existing SQLite file read-only; None when it does not exist.

    `mode=ro` makes SQLite refuse every write statement; the file, its parent folder
    and any -wal / -shm sidecar are never created by this call for a missing store."""
    path = Path(path).absolute()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    except sqlite3.OperationalError:
        if not path.is_file():
            return None
        raise
    conn.row_factory = sqlite3.Row
    # SQLite's busy handler overshoots its budget on Windows (measured: timeout=1 waits 1.8 s,
    # because every sleep is rounded up to the 15.6 ms clock tick). Half a second keeps a locked
    # store's 503 under the 1.5 s of FR-14 there, and costs nothing where the clock is finer.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def store(path: Path):
    """Yield a read-only connection (or None when the store is absent); always close."""
    conn = ro_connect(path)
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                       (name,)).fetchone()
    return row is not None


def state_dir(root: Path) -> Path:
    return root / ".agentry" / "state"


def events_db(root: Path) -> Path:
    return state_dir(root) / "events.db"


def memory_db(root: Path) -> Path:
    return root / ".agentry" / "memory" / "memory.db"


def tasks_dir(root: Path) -> Path:
    return root / ".agentry" / "tasks"


def read_json(path: Path) -> dict:
    """A JSON object from `path`; {} when it is absent, unreadable or not an object."""
    try:
        data = json.loads(_try_read(path, READ_CAP_BYTES) or "")
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_path(plugin_root: Path) -> Path:
    """The plugin's own manifest. The one place that spells its location."""
    return plugin_root / ".claude-plugin" / "plugin.json"


def plugin_manifest(plugin_root: Path) -> dict:
    """The plugin's own manifest ({} when absent)."""
    return read_json(manifest_path(plugin_root))


def read_pipeline(root: Path) -> dict:
    return read_json(root / ".agentry" / "pipeline.json")


def lane_db_paths(root: Path) -> dict[str, Path]:
    """Existing run databases by lane: `run.db` is `default`, `run.<lane>.db` is <lane>.

    Files such as `run.db.bak-20261016` do not match the pattern and are ignored. A
    lane declared in pipeline.json but without a database is NOT here (see lane_names)."""
    found: dict[str, Path] = {}
    try:
        entries = sorted(os.scandir(state_dir(root)), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        match = LANE_DB_RE.match(entry.name)
        if match and entry.is_file():
            found.setdefault(match.group(1)[1:] if match.group(1) else DEFAULT_LANE, Path(entry.path))
    return found


def declared_lanes(root: Path) -> dict[str, str]:
    """lane -> repo key from `lanes{}` of pipeline.json; keys starting `_` are comments."""
    raw = read_pipeline(root).get("lanes")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, entry in raw.items():
        if str(name).startswith("_") or not isinstance(entry, dict) \
                or not LANE_NAME_RE.fullmatch(str(name)):
            continue
        repo = entry.get("repo")
        out[str(name)] = repo.strip() if isinstance(repo, str) else ""
    return out


def lane_names(root: Path) -> list[str]:
    """Lanes with a database plus declared lanes; `default` first, then alphabetical."""
    names = set(lane_db_paths(root)) | set(declared_lanes(root))
    return sorted(names, key=lambda n: (n != DEFAULT_LANE, n))


def dial_file(root: Path, name: str, lane: str) -> Path:
    """The per-lane dial file (`mode`, `approvals`, `swarm`) the harness suffixes with
    `.<lane>` for any lane but the default."""
    return state_dir(root) / (name if lane == DEFAULT_LANE else f"{name}.{lane}")


def read_dial(root: Path, name: str, lane: str) -> str:
    return (_try_read(dial_file(root, name, lane), DIAL_CAP_BYTES) or "").strip().lower()


# --------------------------------------------------------------------------- errors

class ApiError(Exception):
    """An error the server answers with `{message, code, errors?}` and `status`."""

    def __init__(self, status: int, code: str, message: str,
                 errors: dict[str, list[str]] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors
        self.headers = headers or {}


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(404, "not_found", message)


def unavailable() -> ApiError:
    return ApiError(503, "unavailable", "A data store is locked or unreadable; retry shortly",
                    headers={"Retry-After": "1"})


def invalid(field: str, message: str) -> ApiError:
    return ApiError(422, "validation_failed", "Invalid request", {field: [message]})


# --------------------------------------------------------------------------- query strings

_PERCENT = re.compile(rb"%([0-9A-Fa-f]{2})")


def percent_decode(text: str) -> str | None:
    """Decode %XX escapes once; None when the result is not valid UTF-8."""
    raw = _PERCENT.sub(lambda m: bytes([int(m.group(1), 16)]), text.encode("utf-8"))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_query(query: str, limit: int = 100) -> dict[str, str]:
    """`a=1&b=2` -> {"a": "1", "b": "2"}; the first occurrence of a name wins. A value whose
    escapes are not valid UTF-8 is kept as written, so the Params validators answer 422."""
    out: dict[str, str] = {}
    for pair in query.split("&")[:limit]:
        if not pair:
            continue
        name, _, value = pair.partition("=")
        name = percent_decode(name.replace("+", " "))
        value = value.replace("+", " ")
        decoded = percent_decode(value)
        if name is not None:
            out.setdefault(name, value if decoded is None else decoded)
    return out


_INT_RE = re.compile(r"-?[0-9]{1,18}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Params:
    """Validated view of a request's query string. Every bad value adds to `errors`;
    check() raises the single 422 listing all of them (FR-12). A blank value is the
    same as an absent one."""

    def __init__(self, raw: dict[str, str]) -> None:
        self.raw = raw
        self.errors: dict[str, list[str]] = {}

    def reject(self, name: str, message: str) -> None:
        self.errors.setdefault(name, []).append(message)

    def has(self, name: str) -> bool:
        return bool(self.raw.get(name, "").strip())

    def text(self, name: str, default: str = "") -> str:
        return self.raw.get(name, "").strip() or default

    def integer(self, name: str, default: int, lo: int, hi: int) -> int:
        value = self.text(name)
        if not value:
            return default
        if not _INT_RE.fullmatch(value):
            self.reject(name, "must be an integer")
            return default
        number = int(value)
        if not lo <= number <= hi:
            self.reject(name, f"must be between {lo} and {hi}")
            return default
        return number

    def choice(self, name: str, allowed, default: str = "") -> str:
        value = self.text(name)
        if not value:
            return default
        if value not in allowed:
            self.reject(name, "must be one of: " + ", ".join(sorted(allowed)))
            return default
        return value

    def match(self, name: str, pattern: re.Pattern, hint: str, default: str = "") -> str:
        value = self.text(name)
        if not value:
            return default
        if not pattern.fullmatch(value):
            self.reject(name, hint)
            return default
        return value

    def boolean(self, name: str, default: bool = False) -> bool:
        value = self.text(name)
        if not value:
            return default
        if value not in ("true", "false"):
            self.reject(name, "must be true or false")
            return default
        return value == "true"

    def date(self, name: str) -> str:
        value = self.text(name)
        if value and not _DATE_RE.fullmatch(value):
            self.reject(name, "must be a date like 2026-10-16")
            return ""
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                self.reject(name, "must be a real calendar date")
                return ""
        return value

    def page(self, default_limit: int, max_limit: int) -> tuple[int, int]:
        return (self.integer("limit", default_limit, 1, max_limit),
                self.integer("offset", 0, 0, MAX_OFFSET))

    def check(self) -> None:
        if self.errors:
            raise ApiError(422, "validation_failed", "Invalid query parameters", self.errors)


def path_id(value: str, field: str = "id") -> str:
    """A task / handoff id from a URL path segment, 422 when malformed."""
    if not TASK_ID_RE.fullmatch(value):
        raise invalid(field, "must look like task-0001")
    return value


# --------------------------------------------------------------------------- time

def iso_z(value) -> str:
    """Any ISO-ish date or timestamp as `YYYY-MM-DDTHH:MM:SSZ` (UTC), "" when absent or
    unreadable. A bare date is midnight UTC, which keeps FR-15 true for task and memory
    rows that only record a day."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if _DATE_RE.fullmatch(text):
            return date.fromisoformat(text).isoformat() + "T00:00:00Z"
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_iso(seconds) -> str:
    try:
        return datetime.fromtimestamp(float(seconds), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- frontmatter

_FENCED = re.compile(r"---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
# The values are stripped afterwards (_scalar): a lazy `(.*?)[ \t]*$` backtracks quadratically.
_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(.*)$")
_LIST_ITEM = re.compile(r"^[ \t]+-[ \t]+(.*)$")


def split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter block, body) of a markdown document; the body is everything after the
    closing fence line, untouched (CRLF and non-ASCII intact). No fence: ("", text)."""
    text = text.lstrip("\ufeff")
    match = _FENCED.match(text)
    if not match:
        return "", text
    return match.group(1) or "", text[match.end():]


def _scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner
    return value


def parse_head(head: str) -> dict:
    """Top-level `key: value` pairs of a frontmatter block. A key with no inline value
    followed by `- item` lines becomes a list; nested mappings are skipped."""
    out: dict = {}
    key = None
    for line in head[:HEAD_CAP_BYTES].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t":
            match = _KEY_LINE.match(line)
            key = match.group(1) if match else None
            if match:
                out[key] = _scalar(match.group(2))
        elif key is not None:
            item = _LIST_ITEM.match(line)
            if item and (out[key] == "" or isinstance(out[key], list)):
                out[key] = [*(out[key] or []), _scalar(item.group(1))]
    return out


def _try_read(path: Path, cap: int) -> str | None:
    try:
        with path.open("rb") as handle:
            return handle.read(cap).decode("utf-8", errors="replace")
    except OSError:
        return None


def read_capped(path: Path, cap: int = READ_CAP_BYTES) -> str:
    """The first `cap` bytes of a file as text with line endings untouched; "" when
    unreadable."""
    return _try_read(path, cap) or ""


def cap_text(text: str, cap: int = BODY_CAP_BYTES) -> tuple[str, bool]:
    """`text` cut to at most `cap` UTF-8 bytes on a character boundary, and whether it
    was cut."""
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text, False
    cut = cap
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut].decode("utf-8", errors="replace"), True


# --------------------------------------------------------------------------- tasks

_task_cache: dict[str, tuple[int, int, dict]] = {}
_head_cache: dict[str, tuple[int, int, dict]] = {}


def cached_parse(entries: list, parse, cache: dict, cap: int = HEAD_CAP_BYTES) -> list:
    """`parse(entry, head_text)` for every os.DirEntry, cached on (mtime, size).

    A listing of a thousand documents then costs a directory scan, not a thousand file
    reads. The misses are read by a small pool: the first read of a fresh file is slow on a
    machine that scans new files (measured: 3 ms each, 3 s for a thousand tasks), and the
    waits overlap. The returned values are shared with the cache and must not be modified.

    A file that vanished before its stat holds `None` in the result (which stays aligned
    with `entries`). A read that failed is parsed as empty but not cached, so the next
    listing reads the file again."""
    stats: list = []
    for entry in entries:
        try:
            stats.append(entry.stat())
        except OSError:
            stats.append(None)
    values: list = [None] * len(entries)
    misses = []
    for i, (entry, stat) in enumerate(zip(entries, stats)):
        if stat is None:
            continue
        hit = cache.get(entry.path)
        if hit and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
            values[i] = hit[2]
        else:
            misses.append(i)

    def load(i: int) -> tuple[int, object]:
        text = _try_read(Path(entries[i].path), cap)
        value = parse(entries[i], text or "")
        if text is not None:
            cache[entries[i].path] = (stats[i].st_mtime_ns, stats[i].st_size, value)
        return i, value

    if len(misses) < PARALLEL_FROM:
        loaded = [load(i) for i in misses]
    else:
        with ThreadPoolExecutor(READ_WORKERS) as pool:
            loaded = list(pool.map(load, misses))
    for i, value in loaded:
        values[i] = value
    return values


def read_heads(entries: list) -> list[dict]:
    """The parsed frontmatter of each DirEntry (see cached_parse)."""
    return cached_parse(entries, lambda _entry, text: parse_head(split_frontmatter(text)[0]), _head_cache)


def _task_fields(task_id: str, text: str) -> dict:
    head, _ = split_frontmatter(text)
    fm = parse_head(head)

    def s(name: str) -> str:
        value = fm.get(name, "")
        return value if isinstance(value, str) else ""

    return {
        "id": task_id,
        "title": s("title"),
        "type": s("type"),
        "priority": s("priority"),
        "epic": s("epic"),
        "spec": s("spec"),
        "assignee": s("assignee"),
        "created": iso_z(s("created")),
        "completed": iso_z(s("completed")),
        "blocked_on": s("blocked_on"),
        "superseded_by": s("superseded_by"),
        "phase": s("phase"),
        "repo": s("repo"),
        "umbrella": s("umbrella").lower() == "true",
        "depends_on": TASK_REF_RE.findall(s("depends_on")),
        "subtasks": TASK_REF_RE.findall(s("subtasks")),
    }


def scan_tasks(root: Path) -> list[dict]:
    """Every task file of the three folders as a plain dict plus its `folder`.

    Parsed frontmatter is cached on (path, mtime, size): a listing of a thousand tasks
    then costs a directory scan, not a thousand file reads. The returned dicts are shared
    with the cache; callers must not modify them."""
    found = []
    for folder in FOLDERS:
        try:
            listing = list(os.scandir(tasks_dir(root) / folder))
        except OSError:
            continue
        found += [(folder, entry) for entry in listing
                  if TASK_FILE_RE.match(entry.name) and entry.is_file()]
    fields = cached_parse([entry for _, entry in found],
                          lambda entry, text: _task_fields(entry.name[:-3], text), _task_cache)
    return [{**item, "folder": folder} for (folder, _), item in zip(found, fields) if item is not None]


def task_sort_key(task: dict) -> tuple[int, str]:
    return int(task["id"][5:]), task["id"]


def task_index(tasks: list[dict]) -> dict[str, dict]:
    """id -> task; when one id sits in several folders the earlier folder wins, the way
    state.task_dir resolves it."""
    index: dict[str, dict] = {}
    for task in tasks:
        index.setdefault(task["id"], task)
    return index


def task_ref(task_id: str, index: dict[str, dict]) -> dict:
    task = index.get(task_id)
    return {"id": task_id, "title": task["title"] if task else "",
            "folder": task["folder"] if task else ""}


def find_task_file(root: Path, task_id: str) -> tuple[str, Path] | None:
    for folder in FOLDERS:
        path = tasks_dir(root) / folder / f"{task_id}.md"
        if path.is_file():
            return folder, path
    return None


def scan_epics(root: Path) -> list[dict]:
    """Epic files `epic-*.md` except `*-thread.md`, as {id, title, status, tasks: [ids]}."""
    out = []
    try:
        entries = sorted(os.scandir(tasks_dir(root) / "epics"), key=lambda e: e.name)
    except OSError:
        return out
    for entry in entries:
        match = re.match(r"^(epic-\d+)(?:-.*)?\.md$", entry.name)
        if not match or entry.name.endswith("-thread.md") or not entry.is_file():
            continue
        head, _ = split_frontmatter(read_capped(Path(entry.path), HEAD_CAP_BYTES))
        fm = parse_head(head)
        listed = fm.get("tasks", "")
        out.append({
            "id": match.group(1),
            "title": fm.get("title", "") if isinstance(fm.get("title"), str) else "",
            "status": fm.get("status", "") if isinstance(fm.get("status"), str) else "",
            "tasks": TASK_REF_RE.findall(listed if isinstance(listed, str) else " ".join(listed)),
        })
    return out

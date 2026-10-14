#!/usr/bin/env python3
"""Project memory store - SQLite + FTS5, queried at dispatch instead of injected whole.

Memory used to be markdown (memory/lessons.md, patterns.md, codebase.md and a
per-agent agent-memory/<agent>/MEMORY.md) and injection took the HEAD of a file
under a size cap. What an agent received was whatever sat at the top, not what
its task needed: on a real project the lessons layer reached 150KB and went into
every dev dispatch nearly whole. This store replaces the file head with a
ranked query (see inject.py).

Three kinds of row, one FTS5 index over their text:

  lesson   a mistake never to repeat: signature, trigger, what, why, fix
  pattern  a code shape used more than once: name, use_when, body
  module   one row of the L1 module map: path, responsibility, symbols, notes

The store lives at .agentry/memory/memory.db - gitignored, like the markdown it
replaces, because it holds one project's internals and this repo is public.
Standard library only (sqlite3 with FTS5; verified present in the bundled
interpreter). Export to markdown when a human needs to read it.

CLI:
    memory.py --record --kind lesson --signature s --trigger t --what w --why y --fix f
              [--area a] [--task task-0044] [--agent name]
    memory.py --record --kind pattern --name n --use-when u [--body b] [--task t]
    memory.py --record --kind module --path p --responsibility r [--symbols s] [--notes n]
    memory.py --refine --signature s [--agent a] [--trigger t] [--what w] [--why y]
              [--fix f] [--area a] [--task task-0044]       correct a lesson in place
    memory.py --retire --signature s [--agent a] --reason r  withdraw a lesson from retrieval
    memory.py --query "free text" [--limit 8] [--kinds lesson,pattern]
    memory.py --export [--out .agentry/memory/export.md]
    memory.py --stats
    memory.py --migrate [--dry-run]      one-off markdown -> store migration
    memory.py --db PATH                  override the store location (tests)

A wrong lesson is corrected with --refine (only the fields given change, the
row keeps its id and signature) and withdrawn with --retire. Retire sets a flag
and drops the row from the FTS index instead of deleting it: retrieval reads
only the index, so a retired row is never injected again, and the table still
says it was once believed and why it was withdrawn. A delete would erase that.

Record rejects free-form prose: every field is single-line, bounded, and the
five lesson fields are all mandatory. Reads fail open (a missing or corrupt
store answers "no rows"); writes report the failure, since a lost lesson that
nobody notices is the thing this store exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# tools/paths.py decides the project root; tools/ is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths

ROOT = paths.PROJECT_ROOT
MEMORY_DIR = ROOT / ".agentry" / "memory"
DB_PATH = MEMORY_DIR / "memory.db"

KINDS = ("lesson", "pattern", "module")
MAX_FIELD = 2000
SIGNATURE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
# Escaped, not literal: the project rule bans the characters themselves, and a
# literal here is also flagged as ambiguous punctuation by the linter.
DASH_RE = re.compile("[\u2013\u2014]")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id        INTEGER PRIMARY KEY,
    signature TEXT NOT NULL,
    trigger   TEXT NOT NULL,
    what      TEXT NOT NULL,
    why       TEXT NOT NULL,
    fix       TEXT NOT NULL,
    area      TEXT NOT NULL DEFAULT '',
    task      TEXT NOT NULL DEFAULT '',
    agent     TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL,
    retired        TEXT NOT NULL DEFAULT '',
    retired_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS lessons_sig ON lessons(signature, agent);
CREATE TABLE IF NOT EXISTS patterns (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    use_when TEXT NOT NULL,
    body     TEXT NOT NULL DEFAULT '',
    task     TEXT NOT NULL DEFAULT '',
    created  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS patterns_name ON patterns(name);
CREATE TABLE IF NOT EXISTS modules (
    id             INTEGER PRIMARY KEY,
    path           TEXT NOT NULL,
    responsibility TEXT NOT NULL,
    symbols        TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    updated        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS modules_path ON modules(path);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- porter over unicode61: the query is prose written by whoever dispatched the
-- task, so "parked" has to reach a lesson that says "parks". Without stemming
-- retrieval misses on word form alone.
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    kind UNINDEXED, ref UNINDEXED, title, text, tokenize='porter unicode61'
);
"""

TABLE = {"lesson": "lessons", "pattern": "patterns", "module": "modules"}


class MemoryError_(Exception):
    """Refused write - the message is for the caller, not a stack trace."""


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #

def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_retired_columns(conn)
    return conn


def _add_retired_columns(conn: sqlite3.Connection) -> None:
    """A store created before --retire has no retired columns, and CREATE TABLE
    IF NOT EXISTS leaves an existing table alone. Write path only: the readonly
    retrieval path never touches them (a retired row is simply not in the index)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(lessons)")}
    for col in ("retired", "retired_reason"):
        if col not in have:
            conn.execute(f"ALTER TABLE lessons ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.commit()


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection | None:
    """Open an EXISTING store for reading. Returns None when the store is
    missing or unreadable - the retrieval path must never create or block."""
    p = Path(path) if path else DB_PATH
    if not p.is_file():
        return None
    conn = None
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT count(*) FROM mem_fts")  # corrupt store fails here
        return conn
    except Exception:
        if conn is not None:
            conn.close()  # leaving it open would pin the file on Windows
        return None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# validation - this is what "no free-form prose" means in code
# --------------------------------------------------------------------------- #

def field(name: str, value: str | None, required: bool = True) -> str:
    v = (value or "").strip()
    if not v:
        if required:
            raise MemoryError_(f"refused: --{name.replace('_', '-')} is required and must not be empty")
        return ""
    if "\n" in v or "\r" in v:
        raise MemoryError_(f"refused: --{name.replace('_', '-')} must be a single line, "
                           f"not a paragraph - record structured fields, not prose")
    if len(v) > MAX_FIELD:
        raise MemoryError_(f"refused: --{name.replace('_', '-')} is {len(v)} chars, "
                           f"max {MAX_FIELD} - shorten it to one checkable sentence")
    if DASH_RE.search(v):
        raise MemoryError_(f"refused: --{name.replace('_', '-')} contains an em/en dash; "
                           f"use a plain hyphen (rules/quality-standard.md)")
    return v


def signature(value: str | None) -> str:
    v = field("signature", value)
    if not SIGNATURE_RE.match(v):
        raise MemoryError_("refused: --signature must be a kebab-case tag naming the SITUATION "
                           f"(3-80 chars of [a-z0-9._-]), got {v!r}")
    return v


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #

def _index(conn: sqlite3.Connection, kind: str, rowid: int, title: str, text: str) -> None:
    ref = f"{kind}:{rowid}"
    conn.execute("DELETE FROM mem_fts WHERE ref = ?", (ref,))
    conn.execute("INSERT INTO mem_fts(kind, ref, title, text) VALUES (?, ?, ?, ?)",
                 (kind, ref, title, text))


def record_lesson(conn: sqlite3.Connection, *, signature: str, trigger: str, what: str,
                  why: str, fix: str, area: str = "", task: str = "", agent: str = "",
                  created: str = "") -> int:
    """Insert a lesson. Returns its rowid, or 0 when (signature, agent) already
    exists - which makes the migration and any re-record idempotent."""
    row = {
        "signature": _sig(signature),
        "trigger": field("trigger", trigger),
        "what": field("what", what),
        "why": field("why", why),
        "fix": field("fix", fix),
        "area": field("area", area, required=False),
        "task": field("task", task, required=False),
        "agent": field("agent", agent, required=False),
        "created": field("created", created, required=False) or now(),
    }
    cur = conn.execute(
        "INSERT OR IGNORE INTO lessons(signature, trigger, what, why, fix, area, task, agent, created) "
        "VALUES (:signature, :trigger, :what, :why, :fix, :area, :task, :agent, :created)", row)
    if not cur.rowcount:
        return 0
    rowid = int(cur.lastrowid)
    _index(conn, "lesson", rowid, row["signature"], _lesson_text(row))
    conn.commit()
    return rowid


def _lesson_text(row) -> str:
    return (f"WHEN {row['trigger']} | WHAT {row['what']} | WHY {row['why']} | FIX {row['fix']} "
            f"| {row['area']} {row['task']} {row['agent']}")


REFINABLE = ("trigger", "what", "why", "fix", "area", "task")


def _lesson_row(conn: sqlite3.Connection, sig: str, agent: str, verb: str):
    row = conn.execute("SELECT * FROM lessons WHERE signature = ? AND agent = ?",
                       (sig, agent)).fetchone()
    if row is None:
        raise MemoryError_(f"refused: no lesson {sig!r}"
                           + (f" for agent {agent!r}" if agent else "")
                           + f" to {verb}; list rows with --query, or add one with --record")
    return row


def refine_lesson(conn: sqlite3.Connection, *, signature: str, agent: str = "",
                  **changes: str) -> tuple:
    """Correct an existing lesson in place. Only the fields passed (and not empty)
    change; the id and signature stay, so nothing that refers to the row breaks.
    Returns (rowid, [changed field names]). A retired row is refused: reviving a
    withdrawn claim by editing it would hide that it was withdrawn."""
    sig = _sig(signature)
    who = field("agent", agent, required=False)
    new = {k: field(k, v) for k, v in changes.items()
           if k in REFINABLE and (v or "").strip()}
    if not new:
        raise MemoryError_("refused: --refine needs at least one of --"
                           + ", --".join(REFINABLE) + " with the corrected text")
    row = _lesson_row(conn, sig, who, "refine")
    if row["retired"]:
        raise MemoryError_(f"refused: lesson {sig!r} was retired on {row['retired']} "
                           f"({row['retired_reason']}); record the corrected claim "
                           f"under a new signature")
    merged = {**dict(row), **new}
    sets = ", ".join(f"{k} = :{k}" for k in new)
    conn.execute(f"UPDATE lessons SET {sets} WHERE id = :id", {**new, "id": row["id"]})
    _index(conn, "lesson", int(row["id"]), sig, _lesson_text(merged))
    conn.commit()
    return int(row["id"]), list(new)


def retire_lesson(conn: sqlite3.Connection, *, signature: str, reason: str,
                  agent: str = "") -> int:
    """Withdraw a lesson from retrieval without erasing it. Returns its rowid."""
    sig = _sig(signature)
    why = field("reason", reason)
    row = _lesson_row(conn, sig, field("agent", agent, required=False), "retire")
    if row["retired"]:
        raise MemoryError_(f"refused: lesson {sig!r} is already retired ({row['retired']}: "
                           f"{row['retired_reason']})")
    conn.execute("UPDATE lessons SET retired = ?, retired_reason = ? WHERE id = ?",
                 (now(), why, row["id"]))
    conn.execute("DELETE FROM mem_fts WHERE ref = ?", (f"lesson:{row['id']}",))
    conn.commit()
    return int(row["id"])


def record_pattern(conn: sqlite3.Connection, *, name: str, use_when: str, body: str = "",
                   task: str = "", created: str = "") -> int:
    row = {
        "name": field("name", name),
        "use_when": field("use-when", use_when),
        "body": field("body", body, required=False),
        "task": field("task", task, required=False),
        "created": field("created", created, required=False) or now(),
    }
    cur = conn.execute(
        "INSERT OR IGNORE INTO patterns(name, use_when, body, task, created) "
        "VALUES (:name, :use_when, :body, :task, :created)", row)
    if not cur.rowcount:
        return 0
    rowid = int(cur.lastrowid)
    _index(conn, "pattern", rowid, row["name"],
           f"USE WHEN {row['use_when']} | {row['body']} | {row['task']}")
    conn.commit()
    return rowid


def record_module(conn: sqlite3.Connection, *, path: str, responsibility: str,
                  symbols: str = "", notes: str = "") -> int:
    """Upsert one module-map row (the L1 layer). Re-recording a path updates it:
    the map tracks the tree, so the newest row is the true one."""
    row = {
        "path": field("path", path),
        "responsibility": field("responsibility", responsibility),
        "symbols": field("symbols", symbols, required=False),
        "notes": field("notes", notes, required=False),
        "updated": now(),
    }
    conn.execute(
        "INSERT INTO modules(path, responsibility, symbols, notes, updated) "
        "VALUES (:path, :responsibility, :symbols, :notes, :updated) "
        "ON CONFLICT(path) DO UPDATE SET responsibility=excluded.responsibility, "
        "symbols=excluded.symbols, notes=excluded.notes, updated=excluded.updated", row)
    rowid = int(conn.execute("SELECT id FROM modules WHERE path = ?", (row["path"],)).fetchone()[0])
    _index(conn, "module", rowid, row["path"],
           f"{row['responsibility']} | {row['symbols']} | {row['notes']}")
    conn.commit()
    return rowid


def _sig(value: str | None) -> str:
    return signature(value)


# --------------------------------------------------------------------------- #
# meta (L1 freshness heads live here, not in a sidecar json)
# --------------------------------------------------------------------------- #

def meta_get(conn: sqlite3.Connection, key: str, default=None):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        return default


def meta_set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))
    conn.commit()


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #

def counts(conn: sqlite3.Connection | None = None) -> dict:
    """{'lesson': n, 'pattern': n, 'module': n}. Fail-open to zeros so a caller
    can count rows without a try/except of its own."""
    own = conn is None
    if own:
        conn = connect_readonly()
        if conn is None:
            return {k: 0 for k in KINDS}
    out = {}
    try:
        for kind, table in TABLE.items():
            out[kind] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    except Exception:
        out = {k: out.get(k, 0) for k in KINDS}
    finally:
        if own:
            conn.close()
    return out


STOPWORDS = frozenset("""
about after again against also because been before being between both cannot
could does doing done down during each else even ever every from have having
here html into itself just like make made many more most must never next none
only other over same should since some such than that their them then there
these they this those through under until very what when where which while
with without would your yours task tasks file files code test tests line lines
rather instead which about into onto read reads write writes running report
""".split())


def fts_terms(text: str, limit: int = 32) -> list:
    """Content words from free text, in order of first appearance - the query is
    the dispatched task, which is prose, and FTS5 MATCH is a query language, not
    a text box. Order matters because the list is truncated: prose states its
    subject early, so first-seen keeps 'fts5' and drops 'superseded_by'. Sorting
    by length instead spent the budget on long frontmatter keys."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text or "")
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
        if len(out) >= limit:
            break
    return out


def fts_query(text: str, limit_terms: int = 32) -> str:
    return " OR ".join(f'"{t}"' for t in fts_terms(text, limit_terms))


def query(text: str, limit: int = 8, kinds=None, conn: sqlite3.Connection | None = None) -> list:
    """Ranked matches for free text. Returns [{'kind','title','text','score'}].
    Fail-open: a missing, empty or corrupt store returns []."""
    own = conn is None
    if own:
        conn = connect_readonly()
        if conn is None:
            return []
    try:
        match = fts_query(text)
        if not match:
            return []
        sql = ("SELECT kind, title, text, bm25(mem_fts) AS score FROM mem_fts "
               "WHERE mem_fts MATCH ?")
        params: list = [match]
        wanted = [k for k in (kinds or []) if k in KINDS]
        if wanted:
            sql += f" AND kind IN ({','.join('?' * len(wanted))})"
            params += wanted
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in conn.execute(sql, params)]
    except Exception:
        return []
    finally:
        if own:
            conn.close()


def rows(conn: sqlite3.Connection, kind: str) -> list:
    order = {"lesson": "created, signature", "pattern": "name", "module": "path"}[kind]
    return [dict(r) for r in conn.execute(f"SELECT * FROM {TABLE[kind]} ORDER BY {order}")]


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

def export_markdown(conn: sqlite3.Connection) -> str:
    c = counts(conn)
    out = [f"# Project memory export ({now()})", "",
           (f"Store: `.agentry/memory/memory.db` - {c['lesson']} lessons, "
            f"{c['pattern']} patterns, {c['module']} module rows."),
           ("Generated by `memory.py --export`. The store is the source of truth; "
            "this file is for reading, not for editing."), ""]
    out.append("## Lessons")
    out.append("")
    lessons = rows(conn, "lesson")
    for r in (x for x in lessons if not x["retired"]):
        tags = " ".join(t for t in (r["area"], r["task"], r["agent"]) if t)
        out += [f"### {r['signature']}",
                f"- **Trigger:** {r['trigger']}",
                f"- **What:** {r['what']}",
                f"- **Why:** {r['why']}",
                f"- **Fix:** {r['fix']}",
                f"- **Recorded:** {r['created']} {tags}".rstrip(), ""]
    retired = [x for x in lessons if x["retired"]]
    if retired:
        out += ["## Retired lessons (no longer injected)", ""]
        out += [f"- **{r['signature']}** - retired {r['retired']}: {r['retired_reason']}"
                for r in retired]
        out.append("")
    out += ["## Patterns", ""]
    for r in rows(conn, "pattern"):
        out.append(f"- **{r['name']}** - use when {r['use_when']}"
                   + (f" | {r['body']}" if r["body"] else "")
                   + (f" ({r['task']})" if r["task"] else ""))
    out += ["", "## Module map", "",
            "| Path | Responsibility | Key symbols | Notes |", "|---|---|---|---|"]
    for r in rows(conn, "module"):
        out.append(f"| `{r['path']}` | {r['responsibility']} | {r['symbols']} | {r['notes']} |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# migration: markdown -> store (one-off, idempotent)
# --------------------------------------------------------------------------- #

LESSON_H_RE = re.compile(r"^##\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+\*\*(What|Why|Fix|Date|Trigger)\:?\*\*\:?\s*(.*)$", re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"^---\s*$")
L2_RE = re.compile(r"^-\s*\[L-\d+\]\s*(\S+)?\s*([^:]*):\s*(.+?)\s*->\s*(.+?)\s*(?:\(([^)]*)\))?\s*$")
L3_RE = re.compile(r"^-\s*\[P-\d+\]\s*([^:]+):\s*use when\s*(.+?)\s*(?:->\s*(\S+))?\s*$",
                   re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
PLACEHOLDER = ("one sentence", "FILL-ME", "YYYY-MM-DD", "imperative, specific, checkable")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u2014", "-").replace("\u2013", "-")).strip()


def _is_placeholder(*values: str) -> bool:
    joined = " ".join(values)
    return any(p in joined for p in PLACEHOLDER)


def parse_agent_memory(text: str) -> list:
    """Lessons in the per-agent MEMORY.md shape:
        ## [signature / trigger]       (or '## signature')
        - **What:** ... **Why:** ... **Fix:** ... **Date:** ...
    Continuation lines belong to the open bullet. The template block and
    '_No lessons recorded yet._' are skipped."""
    lessons, cur, key = [], None, None

    def close():
        if cur and cur.get("what") and not _is_placeholder(cur.get("what", ""), cur.get("why", "")):
            lessons.append(cur)

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        head = LESSON_H_RE.match(line)
        if head:
            close()
            title = head.group(1).strip().strip("[]")
            # " / " with spaces, not "/": the signature itself may name a path
            # ("a-rule-written-in-rules/*.md"), which a bare slash split in two.
            sig, _, trig = title.partition(" / ")
            # Not every legacy heading carried a trigger; a lesson without one
            # still has to record, so it gets the widest trigger there is.
            cur = {"signature": _slug(sig), "trigger": _flat(trig) or "working in this project",
                   "what": "", "why": "", "fix": "", "date": ""}
            key = None
            continue
        if cur is None:
            continue
        bullet = BULLET_RE.match(line.strip())
        if bullet:
            key = bullet.group(1).lower()
            cur[key] = _flat(bullet.group(2))
            continue
        if key and line.strip() and not line.strip().startswith(("#", "-", "_")):
            cur[key] = _flat(f"{cur[key]} {line}")
    close()
    return [x for x in lessons if x["signature"]]


def parse_frontmatter_memory(text: str, fallback_sig: str) -> dict | None:
    """The other per-agent shape: YAML frontmatter (name/description) plus prose
    carrying '**Why:**' and '**How to apply:**' lines. Mapped onto a lesson so
    nothing is dropped: description -> trigger, first paragraph -> what."""
    lines = (text or "").splitlines()
    if not lines or not FRONTMATTER_RE.match(lines[0]):
        return None
    name, description, body_start = "", "", len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if FRONTMATTER_RE.match(line):
            body_start = i + 1
            break
        if line.lower().startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.lower().startswith("description:"):
            description = line.split(":", 1)[1].strip()
    body = "\n".join(lines[body_start:]).strip()
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    why = fix = ""
    what = ""
    for p in paras:
        flat = _flat(p)
        low = flat.lower()
        if low.startswith("**why:**"):
            why = _flat(flat.split("**", 2)[-1].lstrip(": "))
        elif low.startswith("**how to apply:**"):
            fix = _flat(flat.split("**", 2)[-1].lstrip(": "))
        elif not what:
            what = flat
    if not what:
        return None
    return {"signature": _slug(name or fallback_sig),
            "trigger": _flat(description) or "reading this project's memory",
            "what": what[:MAX_FIELD],
            "why": (why or "recorded as project context, no root cause given")[:MAX_FIELD],
            "fix": (fix or "apply as project context when planning work here")[:MAX_FIELD],
            "date": ""}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _flat(text).lower()).strip("-")
    return s[:80]


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> list:
    """Walk every legacy markdown file and record what it holds. Idempotent:
    a second run inserts nothing. Returns per-file counts."""
    report = []
    agent_dir = ROOT / ".claude" / "agent-memory"
    for path in sorted(agent_dir.glob("*/*.md")) if agent_dir.is_dir() else []:
        agent = path.parent.name
        text = path.read_text(encoding="utf-8", errors="replace")
        found = parse_agent_memory(text)
        fm = parse_frontmatter_memory(text, path.stem)
        if fm:
            found = [fm]
        inserted = skipped = 0
        for lesson in found:
            if dry_run:
                inserted += 1
                continue
            rowid = record_lesson(conn, signature=lesson["signature"],
                                  trigger=lesson["trigger"], what=lesson["what"],
                                  why=lesson["why"] or "not recorded",
                                  fix=lesson["fix"] or "not recorded", agent=agent,
                                  created=lesson.get("date") or "")
            inserted += 1 if rowid else 0
            skipped += 0 if rowid else 1
        report.append({"source": _rel(path), "found": len(found),
                       "inserted": inserted, "skipped_existing": skipped})

    for name, kind in (("lessons.md", "lesson"), ("patterns.md", "pattern"),
                       ("codebase.md", "module")):
        path = MEMORY_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = inserted = skipped = 0
        for line in text.splitlines():
            line = line.strip()
            rowid = 0
            if kind == "lesson":
                m = L2_RE.match(line)
                if not m:
                    continue
                date, area, mistake, rule, task = (m.group(i) or "" for i in range(1, 6))
                found += 1
                if dry_run:
                    inserted += 1
                    continue
                rowid = record_lesson(conn, signature=_slug(mistake) or f"l2-{found}",
                                      trigger=_flat(area) or "working in this project",
                                      what=_flat(mistake), why="not recorded in the L2 one-liner",
                                      fix=_flat(rule), area=_flat(area), task=_flat(task),
                                      created=_flat(date))
            elif kind == "pattern":
                m = L3_RE.match(line)
                if not m:
                    continue
                found += 1
                if dry_run:
                    inserted += 1
                    continue
                rowid = record_pattern(conn, name=_slug(m.group(1)),
                                       use_when=_flat(m.group(2)), body=_flat(m.group(3) or ""))
            else:
                m = TABLE_ROW_RE.match(line)
                if not m:
                    continue
                cells = [c.strip().strip("`") for c in m.group(1).split("|")]
                if len(cells) < 2 or _is_placeholder(*cells) or cells[0].lower() in ("path", ""):
                    continue
                if set("".join(cells)) <= set("-: "):
                    continue
                found += 1
                if dry_run:
                    inserted += 1
                    continue
                rowid = record_module(conn, path=cells[0], responsibility=cells[1],
                                      symbols=cells[2] if len(cells) > 2 else "",
                                      notes=cells[3] if len(cells) > 3 else "")
            if not dry_run:
                inserted += 1 if rowid else 0
                skipped += 0 if rowid else 1
        report.append({"source": _rel(path), "found": found, "inserted": inserted,
                       "skipped_existing": skipped})
    return report


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# Where --out and --db may never point: --export --out overwrites the file it names and
# --db creates one, so a target in the state tree, on pipeline.json, under .claude/ or
# under the plugin root replaces an approval record, the gate configuration or gate code
# (a broken gate script exits non-2, which is an allow). agent_gate.py refuses these for an agent before the
# call; this is the second layer for a caller the gate does not see.
PROTECTED_TARGETS = (".agentry/state", ".agentry/pipeline.json", ".claude")


def _protected_target(raw: str) -> str:
    """Why `raw` may not be written, else ''. It resolves into one of PROTECTED_TARGETS
    (by name, case, `..`, a junction or an NTFS stream suffix, or as a hard link to
    pipeline.json), or it cannot be resolved at all: nothing legitimate is spelled so."""
    unresolvable = "not a path that can be resolved"
    if "\x00" in raw:
        return unresolvable
    try:
        drive, rest = os.path.splitdrive(raw.strip())
        clean = drive + "/".join(p.split(":", 1)[0] for p in rest.replace("\\", "/").split("/"))
        real = os.path.realpath(clean)
        low = real.replace("\\", "/").lower()
        # The gate code now lives under the plugin root, which is no longer inside
        # .claude/ (and may be outside the project altogether).
        guarded = [(r, ROOT / r) for r in PROTECTED_TARGETS]
        guarded.append(("the plugin root", paths.PLUGIN_ROOT))
        for rel, where in guarded:
            base_path = os.path.realpath(where)
            base = base_path.replace("\\", "/").lower()
            if low == base or low.startswith(base + "/"):
                return f"inside {rel}"
            if os.path.isfile(real) and os.path.isfile(base_path) and os.path.samefile(real, base_path):
                return f"inside {rel}"
    except (OSError, ValueError):
        return unresolvable
    return ""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _record(conn: sqlite3.Connection, args) -> int:
    if args.kind == "lesson":
        rowid = record_lesson(conn, signature=args.signature, trigger=args.trigger,
                              what=args.what, why=args.why, fix=args.fix, area=args.area,
                              task=args.task, agent=args.agent)
        label = args.signature
    elif args.kind == "pattern":
        rowid = record_pattern(conn, name=args.name, use_when=args.use_when, body=args.body,
                               task=args.task)
        label = args.name
    else:
        rowid = record_module(conn, path=args.path, responsibility=args.responsibility,
                              symbols=args.symbols, notes=args.notes)
        label = args.path
    if rowid:
        print(f"memory: recorded {args.kind} {label} (id {rowid})")
    elif args.kind == "lesson":
        who = f" --agent {args.agent}" if args.agent else ""
        print(f"memory: lesson {label} already recorded - correct the existing row with: "
              f"memory.py --refine --signature {label}{who} --what <text> "
              f"[--trigger|--why|--fix|--area|--task <text>], or withdraw it with: "
              f"memory.py --retire --signature {label}{who} --reason <text>")
    else:
        print(f"memory: {args.kind} {label} already recorded - nothing changed")
    return 0


def _refine(conn: sqlite3.Connection, args) -> int:
    rowid, changed = refine_lesson(
        conn, signature=args.signature, agent=args.agent, trigger=args.trigger,
        what=args.what, why=args.why, fix=args.fix, area=args.area, task=args.task)
    print(f"memory: REFINED lesson {args.signature} (id {rowid}), changed: {', '.join(changed)}")
    return 0


def _retire(conn: sqlite3.Connection, args) -> int:
    rowid = retire_lesson(conn, signature=args.signature, reason=args.reason, agent=args.agent)
    print(f"memory: RETIRED lesson {args.signature} (id {rowid}); no longer injected, "
          f"row kept in the store")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Project memory store (SQLite + FTS5)")
    parser.add_argument("--db", default="", help="store path (default .agentry/memory/memory.db)")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--refine", action="store_true", help="correct a lesson in place")
    parser.add_argument("--retire", action="store_true", help="withdraw a lesson from retrieval")
    parser.add_argument("--reason", default="", help="--retire: why the lesson is withdrawn")
    parser.add_argument("--kind", choices=KINDS, default="lesson")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--kinds", default="", help="--query filter: lesson,pattern,module")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--out", default="", help="--export target (default memory/export.md)")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    # lesson fields
    parser.add_argument("--signature", default="")
    parser.add_argument("--trigger", default="")
    parser.add_argument("--what", default="")
    parser.add_argument("--why", default="")
    parser.add_argument("--fix", default="")
    parser.add_argument("--area", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--agent", default="")
    # pattern fields
    parser.add_argument("--name", default="")
    parser.add_argument("--use-when", default="", dest="use_when")
    parser.add_argument("--body", default="")
    # module fields
    parser.add_argument("--path", default="")
    parser.add_argument("--responsibility", default="")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)

    for option, value in (("--out", args.out), ("--db", args.db)):
        place = _protected_target(value) if value else ""
        if place:
            print(f"refused: {option} {value!r} is {place}; memory.py never writes there")
            return 1

    db = args.db or None
    if args.query:
        hits = query(args.query, limit=args.limit,
                     kinds=[s.strip() for s in args.kinds.split(",") if s.strip()],
                     conn=connect_readonly(db))
        if not hits:
            print("memory: no matching rows")
            return 0
        for h in hits:
            print(f"[{h['kind']}] {h['title']} (score {h['score']:.2f})")
            print(f"    {h['text']}")
        return 0

    if args.stats:
        c = counts(connect_readonly(db))
        p = Path(db) if db else DB_PATH
        size = p.stat().st_size if p.is_file() else 0
        print(f"store: {_rel(p)} ({size // 1024} KB, {'present' if size else 'missing'})")
        print(f"lessons: {c['lesson']}\npatterns: {c['pattern']}\nmodules: {c['module']}")
        return 0

    conn = connect(db)
    try:
        if args.record:
            return _record(conn, args)
        if args.refine:
            return _refine(conn, args)
        if args.retire:
            return _retire(conn, args)
        if args.migrate:
            report = migrate(conn, dry_run=args.dry_run)
            total = 0
            for r in report:
                total += r["inserted"]
                print(f"{r['source']}: found {r['found']}, inserted {r['inserted']}, "
                      f"already present {r['skipped_existing']}")
            print(f"total inserted: {total}")
            print(json.dumps(counts(conn)))
            return 0
        if args.export:
            out = Path(args.out) if args.out else MEMORY_DIR / "export.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(export_markdown(conn), encoding="utf-8")
            print(f"memory: exported to {_rel(out)}")
            return 0
        parser.print_help()
        return 1
    except MemoryError_ as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

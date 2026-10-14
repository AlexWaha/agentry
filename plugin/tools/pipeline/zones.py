#!/usr/bin/env python3
"""Swarm zones (task-0060 S2): declare, prove disjoint, freeze, look up.

The orchestrator writes a `## Zones` table on the swarm board:

    | zone   | paths                          |
    |--------|--------------------------------|
    | 0060-S2| `plugin/tools/pipeline/zones.py`, `docs/zones/**` |

A path is an exact repo-relative file or `dir/**` (everything below dir). Nothing
else is accepted, so overlap is decidable by comparing path segments.

`--prove --task T` refuses overlapping zones and shared files that a zone only
covers through a `dir/**`, then freezes the map to `.agentry/state/zones.json`
(the state directory is agent-unwritable). The frozen map is honoured only while
the task sits at implement or test. `--release --task T` deletes the frozen map when it
is T's (the orchestrator's way to unfreeze; agent_gate refuses every subagent the
script). `owner_of(path)` and `zone_for_agent(...)` are what agent_gate (S3) calls.

Paths compare case-insensitively with `/` separators, as on the Windows host.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sqlite3
import sys
from pathlib import Path

import state

LIVE_STAGES = ("implement", "test")
# What task_stage() returns when run.db exists but cannot be read (locked, corrupt, a
# permission error). It is not a stage and not None: None means "the task has no run", and
# a reader that took an unreadable database for that would read the map as dormant and
# stop fencing the dev agents (SEC-A M5 of task-0120).
STAGE_UNREADABLE = "<unreadable>"
ZONES_FILE = "zones.json"
TASK_RE = re.compile(r"^task-\d+$")
ZONE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
ZONE_LINE_RE = re.compile(r"^[ \t]*Swarm zone:[ \t]*([A-Za-z0-9][A-Za-z0-9._-]*)[ \t]*$", re.MULTILINE)
# The identity line of a task that works in its own worktree (task-0134): a task id alone on
# its line, the same grammar as the zone line. A path, a second value or a mid-line mention
# does not match, so the line cannot be forged by text that merely mentions it.
TASK_LINE_RE = re.compile(r"^[ \t]*Task worktree:[ \t]*(task-[0-9]+)[ \t]*$", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FORBIDDEN_CHARS = set('*?[]:<>|"')
MAX_TRANSCRIPT_LINE = 4 * 1024 * 1024

# Used when pipeline.json has no swarm.shared_paths. Exact files, plus the task
# folders (many files, written by every agent that records evidence).
DEFAULT_SHARED = (
    ".claude/settings.json", ".agentry/pipeline.json", ".agentry/tasks/**",
    "package.json", "package-lock.json", "composer.json", "composer.lock",
    "requirements.txt", "pyproject.toml", "uv.lock", "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum", "Gemfile", "Gemfile.lock",
)


# --- paths -------------------------------------------------------------------

def parse_path(raw: str) -> tuple[bool, str]:
    """(is_glob, normalised path). ValueError names why a path is not allowed."""
    p = raw.strip().strip("`").strip().replace("\\", "/")
    p = p.removeprefix("./")
    is_glob = p.endswith("/**")
    body = p[:-3] if is_glob else p
    if not body:
        raise ValueError(f"{raw!r}: needs a directory before /** (a bare ** is the whole repo)")
    if body.startswith("/") or re.match(r"^[A-Za-z]:", body):
        raise ValueError(f"{raw!r}: absolute paths are not allowed, use a repo-relative path")
    if body.endswith("/"):
        raise ValueError(f"{raw!r}: write a directory as dir/**")
    if FORBIDDEN_CHARS & set(body) or re.search(r"\s", body):
        raise ValueError(f"{raw!r}: only an exact path or dir/** is allowed")
    if any(seg in ("", ".", "..") for seg in body.split("/")):
        raise ValueError(f"{raw!r}: empty, '.' and '..' segments are not allowed")
    return is_glob, p


def _segs(path: str) -> tuple[str, ...]:
    body = path.removesuffix("/**")
    return tuple(s.casefold() for s in body.split("/"))


def overlap(a: str, b: str) -> str | None:
    """The path two declarations both claim, or None when they are disjoint."""
    (ga, pa), (gb, pb) = parse_path(a), parse_path(b)
    sa, sb = _segs(pa), _segs(pb)
    if not ga and not gb:
        return pa if sa == sb else None
    if ga and gb:
        narrower = pb if len(sa) <= len(sb) else pa
        return narrower if (sa[:len(sb)] == sb or sb[:len(sa)] == sa) else None
    glob_segs, exact_segs, exact = (sa, sb, pb) if ga else (sb, sa, pa)
    covered = len(exact_segs) > len(glob_segs) and exact_segs[:len(glob_segs)] == glob_segs
    return exact if covered else None


# --- board -------------------------------------------------------------------

def parse_board(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """({zone: [normalised paths]}, errors) from the board's `## Zones` table."""
    zones: dict[str, list[str]] = {}
    errors: list[str] = []
    in_zones = seen = False
    for line in text.splitlines():
        s = line.strip()
        heading = HEADING_RE.match(s)
        if heading:
            if len(heading.group(1)) == 2 and heading.group(2).casefold() == "zones":
                in_zones = seen = True
            elif in_zones and len(heading.group(1)) <= 2:
                in_zones = False
            continue
        if not in_zones or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r"[-: ]*", c) for c in cells):
            continue
        if len(cells) != 2:
            errors.append(f"zone row needs exactly two cells (zone | paths): {s!r}")
            continue
        zone, raw_paths = cells
        if zone.casefold() == "zone" and raw_paths.casefold().startswith("path"):
            continue
        if not ZONE_ID_RE.match(zone):
            errors.append(f"zone id {zone!r} is not allowed (letters, digits, '.', '_', '-')")
            continue
        if zone in zones:
            errors.append(f"zone {zone} is declared twice")
            continue
        paths: list[str] = []
        for piece in raw_paths.split(","):
            if not piece.strip():
                continue
            try:
                paths.append(parse_path(piece)[1])
            except ValueError as exc:
                errors.append(f"zone {zone}: {exc}")
        if not paths:
            errors.append(f"zone {zone} declares no paths")
        zones[zone] = paths
    if not seen:
        errors.append("board has no '## Zones' section")
    elif not zones and not errors:
        errors.append("the '## Zones' table has no rows")
    return zones, errors


def check(zones: dict[str, list[str]], shared: list[str]) -> list[str]:
    """Problems that make the map unsafe to freeze; empty means proven disjoint."""
    problems: list[str] = []
    ids = sorted(zones)
    for i, first in enumerate(ids):
        for second in ids[i + 1:]:
            hits = sorted({h for a in zones[first] for b in zones[second]
                           if (h := overlap(a, b))})
            problems.extend(f"zones {first} and {second} overlap on {h}" for h in hits)
    for entry in shared:
        is_glob = parse_path(entry)[0]
        named = any(not parse_path(p)[0] and overlap(p, entry) for ps in zones.values() for p in ps)
        for zone in ids:
            for p in zones[zone]:
                if not parse_path(p)[0] or not overlap(p, entry):
                    continue
                if is_glob:
                    problems.append(f"shared directory {entry} overlaps the glob {p} of zone "
                                    f"{zone}; name files in it exactly or narrow the glob")
                elif not named:
                    problems.append(f"shared file {entry} is covered only by the glob {p} of "
                                    f"zone {zone}; name it exactly in one zone or narrow the glob")
    return problems


def shared_paths() -> list[str]:
    """pipeline.json `swarm.shared_paths`, else DEFAULT_SHARED. ValueError when a
    configured entry is not an exact path or dir/**."""
    swarm = state.load_pipeline().get("swarm")
    configured = swarm.get("shared_paths") if isinstance(swarm, dict) else None
    entries = configured if isinstance(configured, list) else list(DEFAULT_SHARED)
    return [parse_path(str(e))[1] for e in entries]


# --- frozen map --------------------------------------------------------------

def zones_path() -> Path:
    return Path(state.STATE_DIR) / ZONES_FILE


def swarm_dir(task: str) -> Path | None:
    """The swarm state dir of `task` (board, QA reports), resolved under state.ROOT the
    way `.agentry` is; None when `task` is not a task id, so a damaged map cannot name
    a wider directory."""
    if not TASK_RE.match(str(task)):
        return None
    return Path(state.ROOT) / ".agentry" / "tasks" / "swarm" / task


def board_path(task: str) -> Path:
    return Path(state.ROOT) / ".agentry" / "tasks" / "swarm" / task / "board.md"


def task_stage(task: str) -> str | None:
    """The task's current stage, read without taking a write lock; None when there is no
    run.db or the task has no run, STAGE_UNREADABLE when run.db exists and cannot be read."""
    db = Path(state.DB_PATH)
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT stage FROM runs WHERE task = ?", (task,)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return STAGE_UNREADABLE
    return row[0] if row else None


def load() -> dict | None:
    """The frozen map, or None when there is none, it is unreadable, or its task
    is no longer at implement/test (or its stage cannot be read). None is never a
    licence: S3 denies on it unless the map is well-formed AND its task was read at a
    stage outside implement/test (agent_gate.map_is_dormant)."""
    try:
        data = json.loads(zones_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not (isinstance(data, dict) and isinstance(data.get("task"), str)
            and isinstance(data.get("zones"), dict)):
        return None
    if task_stage(data["task"]) not in LIVE_STAGES:
        return None
    return data


def _relative(path) -> str | None:
    p = str(path).replace("\\", "/")
    root = Path(state.ROOT).as_posix().rstrip("/") + "/"
    if p.casefold().startswith(root.casefold()):
        p = p[len(root):]
    elif p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    p = posixpath.normpath(p)
    return None if p.startswith("..") or p == "." else p


def owner_of(path, data: dict | None = None) -> str | None:
    """The zone that owns `path` in the frozen map, or None."""
    data = load() if data is None else data
    rel = _relative(path)
    if not data or rel is None:
        return None
    target = tuple(s.casefold() for s in rel.split("/"))
    for zone, paths in data["zones"].items():
        for declared in paths:
            segs = _segs(declared)
            if declared.endswith("/**"):
                if len(target) > len(segs) and target[:len(segs)] == segs:
                    return zone
            elif target == segs:
                return zone
    return None


def dispatch_prompt(agent_id: str, transcript_dir) -> str | None:
    """The dispatch prompt of ONE agent: the text of line 1 of its own transcript,
    `<transcript_dir>/agent-<agent_id>.jsonl`. The transcript_dir is `<parent transcript
    dir>/<session_id>/subagents`; the parent transcript names every agent's zone and task
    and is never read. None on any doubt."""
    if not AGENT_ID_RE.match(str(agent_id or "")):
        return None
    try:
        with (Path(transcript_dir) / f"agent-{agent_id}.jsonl").open("rb") as fh:
            first = json.loads(fh.readline(MAX_TRANSCRIPT_LINE))
    except (OSError, ValueError, RecursionError):
        return None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(b["text"] for b in content
                            if isinstance(b, dict) and isinstance(b.get("text"), str))
    return content if isinstance(content, str) else None


def _single_line_value(pattern: re.Pattern, text: str | None) -> str | None:
    """The one distinct value `pattern` finds in `text`; None for none or for several."""
    found = set(pattern.findall(text or ""))
    return found.pop() if len(found) == 1 else None


def zone_for_agent(agent_id: str, transcript_dir) -> str | None:
    """The `Swarm zone: <id>` of the dispatch prompt (see dispatch_prompt). None on any
    doubt."""
    return _single_line_value(ZONE_LINE_RE, dispatch_prompt(agent_id, transcript_dir))


def worktree_task_for_agent(agent_id: str, transcript_dir) -> str | None:
    """The `Task worktree: <task-NNNN>` of the dispatch prompt (see dispatch_prompt): the
    task whose worktree this agent works in. None on any doubt, and for two different
    values."""
    return _single_line_value(TASK_LINE_RE, dispatch_prompt(agent_id, transcript_dir))


# --- commands ----------------------------------------------------------------

def prove(task: str) -> tuple[bool, list[str]]:
    """Check the task's board and freeze it. (False, reasons) when refused."""
    if not TASK_RE.match(task):
        return False, [f"{task!r} is not a task id (task-NNNN)"]
    stage = task_stage(task)
    if stage not in LIVE_STAGES:
        where = ("a stage that cannot be read (run.db is locked or damaged)"
                 if stage == STAGE_UNREADABLE else stage or "no stage (not registered)")
        allowed = " or ".join(LIVE_STAGES)
        return False, [f"{task} is at {where}; zones can only be frozen at {allowed}"]
    try:
        text = board_path(task).read_text(encoding="utf-8")
    except OSError as exc:
        return False, [f"cannot read the board: {exc}"]
    zones, problems = parse_board(text)
    try:
        shared = shared_paths()
    except ValueError as exc:
        return False, [f"swarm.shared_paths in pipeline.json: {exc}"]
    if not problems:
        problems = check(zones, shared)
    if problems:
        return False, problems
    target = zones_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"task": task, "frozen_at": state.now(), "zones": zones,
                               "shared": shared}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return True, [f"froze {len(zones)} zone(s) for {task} to {target}"]


def release(task: str) -> tuple[bool, list[str]]:
    """Delete the frozen map when it is `task`'s. Refused for another task's map, for a
    map that cannot be read (whose owner is unknown), and for text that is not a task id.
    No map at all is not an error: the state asked for already holds."""
    if not TASK_RE.match(task):
        return False, [f"{task!r} is not a task id (task-NNNN)"]
    target = zones_path()
    if not target.exists():
        return True, [f"no frozen map at {target}; nothing to release"]
    try:
        owner = json.loads(target.read_text(encoding="utf-8")).get("task")
    except (OSError, ValueError, RecursionError, AttributeError) as exc:
        why = (f"cannot tell whose map {target} is ({exc.__class__.__name__}); freeze a new one "
               f"with --prove or remove the file by hand")
        return False, [why]
    if owner != task:
        return False, [f"the frozen map belongs to {owner!r}, not {task}; use that task's id"]
    target.unlink()
    return True, [f"released the zone map of {task} ({target})"]


def describe() -> list[str]:
    try:
        data = json.loads(zones_path().read_text(encoding="utf-8"))
        task = data["task"]
        zones = data["zones"]
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return [f"no usable {zones_path()}"]
    stage = task_stage(task)
    live = stage in LIVE_STAGES
    lines = [f"{task} at {stage or 'unknown'}: " + ("in force" if live else
             f"NOT in force (needs {' or '.join(LIVE_STAGES)})")]
    if stage == STAGE_UNREADABLE:
        lines[0] = f"{task} at {stage}: run.db cannot be read, dev writes are refused until it can"
    lines += [f"  {zone}: {', '.join(paths)}" for zone, paths in zones.items()]
    lines.append("  shared: " + ", ".join(data.get("shared", [])))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prove", action="store_true", help="check the board and freeze it")
    group.add_argument("--show", action="store_true", help="print the frozen map")
    group.add_argument("--release", action="store_true",
                       help="delete the frozen map when it belongs to --task")
    parser.add_argument("--task", help="umbrella task id (required with --prove / --release)")
    args = parser.parse_args(argv)
    if args.show:
        print("\n".join(describe()))
        return 0
    if not args.task:
        parser.error("--prove and --release need --task")
    ok, lines = (release if args.release else prove)(args.task)
    for line in lines:
        print(line if ok else f"refused: {line}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

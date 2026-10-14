#!/usr/bin/env python3
"""Repository autodetect for onboarding (FR-60, FR-61).

Finds every git repository of a workspace, proposes a stack for each from the
manifests it holds, and - once the User has corrected the proposal - writes the
confirmed table into `repos{}` (contract C-3) and `lanes{}` (contract C-4) of
`.agentry/pipeline.json`. Detect and propose, never assume: a directory with a
`.git` and no recognised manifest is listed with stack `unknown`, not omitted.

The manifest table below is the per-repo form of what `_init-prompt.md` Phase A
step A1 inspects; Phase A reads it per repo, there is no second detector.

The scan walks the workspace root plus one and two levels down, the same depth
`git_state.repos()` and `codebase_sync.py` use. A directory that cannot be read
is reported on stderr and skipped (NFR-4): onboarding never aborts over one.

`--write` edits the config as TEXT. It replaces the value of the top-level
`repos` and `lanes` keys (or inserts them when absent) and copies every other
byte through, so the layout, the key order and the line endings of the file
survive (R7). It reads the result back through `git_state.repo_entries()` and
`state.lanes()` and restores the original bytes if either dropped an entry. The
file is replaced atomically (temp file + os.replace).

`--write` on the LIVE `.agentry/pipeline.json` is orchestrator-only: repos{} holds
the `stages` commands stack_gate.py runs with shell=True. agent_gate.py denies the
command line to every subagent profile, and write() refuses a live config inside
Claude Code unless the orchestrator sets AGENTRY_MAIN_THREAD=1 (see
refuse_unattested_live_write for why that is an attestation, not a detection).

CLI:
    python repo_scan.py --root DIR                    # table for the User
    python repo_scan.py --root DIR --json             # [{key,path,stack,manifests}]
    python repo_scan.py --root DIR --write FILE       # write the scan as is
    python repo_scan.py --root DIR --table T.json --write FILE   # write the corrected table
    AGENTRY_MAIN_THREAD=1 python repo_scan.py ... --write .agentry/pipeline.json   # the live config
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "pipeline"))

import git_state
import state

# Manifest file name -> stack identifier. Order matters: the first manifest found
# names the stack, every manifest found is still listed so the User can see a
# mixed repo and correct the stack.
MANIFESTS = (
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("setup.py", "python"),
    ("Pipfile", "python"),
    ("package.json", "node"),
    ("composer.json", "php"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("Gemfile", "ruby"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("mix.exs", "elixir"),
    ("pubspec.yaml", "dart"),
)
# Manifests named by suffix rather than by file name.
MANIFEST_SUFFIXES = ((".csproj", "dotnet"), (".sln", "dotnet"))
UNKNOWN = "unknown"

SKIP_DIRS = {".git", "node_modules", "vendor", ".claude", "storage",
             "dist", "build", "data", "logs"}
MAX_DEPTH = 2

DEFAULT_LANE = "default"

# Claude Code exports CLAUDECODE=1 to every command it runs, a subagent's included.
CLAUDE_ENV = "CLAUDECODE"
MAIN_THREAD_ENV = "AGENTRY_MAIN_THREAD"

_NOT_KEY = re.compile(r"[^a-z0-9_-]+")
_DECODER = json.JSONDecoder()


# --- scan -------------------------------------------------------------------

def _names(directory: Path) -> list[str]:
    with os.scandir(directory) as entries:
        return sorted(e.name for e in entries)


def detect(directory: Path) -> tuple[str, list[str]]:
    """(stack, manifests found) for one repository directory."""
    names = _names(directory)
    found = [(n, s) for n, s in MANIFESTS if n in names]
    found += [(n, s) for suffix, s in MANIFEST_SUFFIXES for n in names if n.endswith(suffix)]
    return (found[0][1] if found else UNKNOWN), [n for n, _ in found]


def _key_for(directory: Path) -> str:
    key = _NOT_KEY.sub("-", directory.name.lower()).lstrip("_-")
    return key or "repo"


def _unique(key: str, taken: set[str]) -> str:
    candidate, n = key, 2
    while candidate in taken:
        candidate, n = f"{key}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _candidates(root: Path) -> list[Path]:
    """The root plus every directory one and two levels down, in a stable order.
    An unreadable directory is reported and not descended into."""
    found = [root]
    level = [root]
    for _ in range(MAX_DEPTH):
        nxt = []
        for parent in level:
            try:
                names = _names(parent)
            except OSError as exc:
                print(f"repo_scan: skipped {parent}: {exc}", file=sys.stderr)
                continue
            for name in names:
                child = parent / name
                if name in SKIP_DIRS or name.startswith(".") or not child.is_dir():
                    continue
                nxt.append(child)
        found += nxt
        level = nxt
    return found


def scan(root: Path) -> list[dict]:
    """Every directory with a `.git`, as [{key, path, stack, manifests}]. `path`
    is workspace-relative and posix-style; the root itself is ".". Never raises
    on an unreadable directory."""
    root = root.resolve()
    rows: list[dict] = []
    taken: set[str] = set()
    for directory in _candidates(root):
        try:
            if not (directory / ".git").exists():
                continue
            stack, manifests = detect(directory)
        except OSError as exc:
            print(f"repo_scan: skipped {directory}: {exc}", file=sys.stderr)
            continue
        rel = directory.relative_to(root).as_posix()
        rows.append({"key": _unique(_key_for(directory), taken), "path": rel,
                     "stack": stack, "manifests": manifests})
    return rows


def render(rows: list[dict]) -> str:
    """The table the User corrects."""
    header = ("key", "path", "stack", "manifests")
    body = [(r["key"], r["path"], r["stack"], ", ".join(r["manifests"]) or "-") for r in rows]
    widths = [max(len(line[i]) for line in [header, *body]) for i in range(4)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip()
             for line in [header, *body]]
    return "\n".join(lines)


# --- the confirmed table -> repos{} and lanes{} ------------------------------

def build_config(rows: list[dict], existing: dict) -> tuple[dict, dict]:
    """(repos value, lanes value) for a confirmed table.

    A row is {key?, path, stack?, stages?, lane?}. Gate commands are not
    something a manifest scan knows, so a row without `stages` keeps the stages
    the config already holds for that key, else none (an explicit pass, FR-43).
    Lanes: a row's own `lane` wins; otherwise the first such row takes `default`
    (it receives tasks with an empty `repo:`) and every later one is named after
    its repo key. Exactly one repo per lane, so a repeated lane name is refused.
    `_`-prefixed comment keys already in the config are carried over."""
    if not rows:
        raise ValueError("the table has no repositories; refusing to write an empty repos{}")
    old_repos = existing.get("repos") if isinstance(existing.get("repos"), dict) else {}
    old_lanes = existing.get("lanes") if isinstance(existing.get("lanes"), dict) else {}
    repos = {k: v for k, v in old_repos.items() if k.startswith("_")}
    lanes = {k: v for k, v in old_lanes.items() if k.startswith("_")}

    taken: set[str] = set()
    named: list[tuple[str, dict]] = []
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str) or not raw["path"].strip():
            raise ValueError(f"every row needs a non-empty string path, got {raw!r}")
        path = raw["path"].replace("\\", "/").strip() or "."
        key = raw.get("key") or _key_for(Path(path))
        if not isinstance(key, str) or not key.strip() or key.startswith("_"):
            raise ValueError(f"repo key {key!r} must be a non-empty string not starting with '_'")
        if key in taken:
            raise ValueError(f"repo key {key!r} appears twice")
        taken.add(key)
        named.append((key, {**raw, "path": path}))

    explicit = {row["lane"] for _, row in named if row.get("lane")}
    default_free = DEFAULT_LANE not in explicit
    for key, row in named:
        previous = old_repos.get(key) if isinstance(old_repos.get(key), dict) else {}
        stages = row.get("stages", previous.get("stages", {}))
        repos[key] = {"path": row["path"], "stack": str(row.get("stack") or UNKNOWN),
                      "stages": stages if isinstance(stages, dict) else {}}
        lane = row.get("lane")
        if not lane and default_free:
            lane, default_free = DEFAULT_LANE, False
        elif not lane:
            lane = key
        if lane in lanes:
            raise ValueError(f"lane {lane!r} is bound to two repositories; a lane has exactly one")
        lanes[lane] = {"repo": key}
    return repos, lanes


# --- text-level edit of pipeline.json (R7) -----------------------------------

def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def top_level_spans(text: str) -> dict[str, tuple[int, int, int]]:
    """key -> (offset of the key, start of its value, end of its value) for the
    top-level keys of a JSON object. The last occurrence wins, as in json.loads."""
    i = _skip_ws(text, 0)
    if text[i:i + 1] != "{":
        raise ValueError("the config is not a JSON object")
    i = _skip_ws(text, i + 1)
    spans: dict[str, tuple[int, int, int]] = {}
    while text[i:i + 1] != "}":
        key, end = _DECODER.raw_decode(text, i)
        j = _skip_ws(text, end)
        if not isinstance(key, str) or text[j:j + 1] != ":":
            raise ValueError("unexpected token at the top level of the config")
        start = _skip_ws(text, j + 1)
        _, stop = _DECODER.raw_decode(text, start)
        spans[key] = (i, start, stop)
        i = _skip_ws(text, stop)
        if text[i:i + 1] == ",":
            i = _skip_ws(text, i + 1)
    return spans


def _line_indent(text: str, offset: int) -> str:
    before = text[text.rfind("\n", 0, offset) + 1:offset]
    return before if not before.strip() else "  "


def _pretty(value: object, indent: str, nl: str) -> str:
    """`value` as multi-line JSON for a top-level key whose line starts with
    `indent`. The key's own indent is the unit, so a tab-indented file stays
    tab-indented. One entry per line, each entry on one line, the way the
    config's own lanes{} is written."""
    if not isinstance(value, dict) or not value:
        return json.dumps(value)
    body = f",{nl}".join(f"{indent}{indent}{json.dumps(k)}: {json.dumps(v)}" for k, v in value.items())
    return f"{{{nl}{body}{nl}{indent}}}"


def splice(text: str, values: dict[str, object], anchor: str = "cwd") -> str:
    """`text` with the top-level keys in `values` set and nothing else changed.

    An existing key keeps the shape it had: a one-line value is rewritten on one
    line, a multi-line value is rewritten indented. A missing key is inserted
    after `anchor` (else after the last key), in the order of `values`. Every
    byte outside those spans, line endings included, is copied through."""
    spans = top_level_spans(text)
    nl = "\r\n" if "\r\n" in text else "\n"
    edits: list[tuple[int, int, str]] = []
    inserts: dict[int, list[str]] = {}
    fallback = anchor if anchor in spans else max(spans, key=lambda k: spans[k][2], default="")
    for key, value in values.items():
        if key in spans:
            key_at, start, stop = spans[key]
            if "\n" in text[start:stop]:
                shown = _pretty(value, _line_indent(text, key_at), nl)
            else:
                shown = json.dumps(value)
            edits.append((start, stop, shown))
            continue
        after = "repos" if key == "lanes" and "repos" in spans else fallback
        if not after:
            raise ValueError("cannot insert a key into an empty config")
        indent = _line_indent(text, spans[after][0])
        shown = _pretty(value, indent, nl)
        inserts.setdefault(spans[after][2], []).append(f',{nl}{indent}"{key}": {shown}')
    for stop, pieces in inserts.items():
        edits.append((stop, stop, "".join(pieces)))
    for start, stop, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + new + text[stop:]
    return text


def read_back(path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """What the pipeline's own readers make of `path`."""
    saved = state.PIPELINE_PATH
    state.PIPELINE_PATH = path
    try:
        return git_state.repo_entries(), state.lanes()
    finally:
        state.PIPELINE_PATH = saved


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data` in one step: a temp file in the same directory,
    then os.replace. A crash or a second writer never leaves the config
    half-written, and the config is never briefly empty (it is read by every
    gate)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_live_config(path: Path) -> bool:
    """True when `path` is the pipeline config the gates read (state.PIPELINE_PATH),
    however it is spelled: relative, through a symlink, with other casing."""
    live = state.PIPELINE_PATH
    try:
        if path.exists() and live.exists():
            return os.path.samefile(path, live)
        return os.path.normcase(path.resolve()) == os.path.normcase(live.resolve())
    except OSError:
        return True  # cannot tell, so treat it as the live one


def refuse_unattested_live_write(path: Path) -> None:
    """Raise ValueError when a subagent could be writing the live pipeline config.

    repos{} carries the `stages` command lists that stack_gate.py runs with
    shell=True, so whoever writes it chooses what the gates execute, and no agent
    profile may. The hook gates stop the command line (agent_gate.py denies
    `repo_scan.py --write` to every profile); this is the in-process half, for a
    caller that reaches write() some other way.

    The gates know a subagent from the hook payload (agent_id / agent_type keys,
    a .../subagents/ transcript path). A script run through Bash receives no
    payload, and CLAUDE_PID, the one session identifier its environment carries,
    is the same in the orchestrator's shell and in a subagent's (busy.py,
    measured), so the caller cannot be told apart from here. Detection is
    unavailable, so inside Claude Code the write is denied unless the
    orchestrator attests the main thread with AGENTRY_MAIN_THREAD=1. A person's
    own terminal (no CLAUDECODE) is not a subagent and writes freely, as does any
    file that is not the live config."""
    if not is_live_config(path):
        return
    if not os.environ.get(CLAUDE_ENV) or os.environ.get(MAIN_THREAD_ENV) == "1":
        return
    raise ValueError(
        f"{path} is the live pipeline config and this runs inside Claude Code, where a "
        f"subagent cannot be told from the orchestrator. Only the orchestrator writes "
        f"it: set {MAIN_THREAD_ENV}=1 for that one command. Nothing was written.")


def write(path: Path, rows: list[dict]) -> tuple[dict, dict]:
    """Write the confirmed table into `path`. Raises ValueError, with the file
    left exactly as it was, on anything it cannot write and verify. Refuses the
    live pipeline config from an unattested caller (refuse_unattested_live_write);
    the file is replaced atomically."""
    if not path.is_file():
        raise ValueError(f"{path} does not exist; this only edits an existing pipeline config")
    refuse_unattested_live_write(path)
    original = path.read_bytes()
    text = original.decode("utf-8")
    existing = json.loads(text)
    if not isinstance(existing, dict):
        raise ValueError(f"{path} is not a JSON object")
    repos, lanes = build_config(rows, existing)
    new = splice(text, {"repos": repos, "lanes": lanes})
    json.loads(new)
    _write_atomic(path, new.encode("utf-8"))
    entries, bound = read_back(path)
    wanted = {k for k in repos if not k.startswith("_")}
    if set(entries) != wanted or bound != {k: v["repo"] for k, v in lanes.items() if not k.startswith("_")}:
        _write_atomic(path, original)
        raise ValueError("the pipeline readers dropped an entry of the table; "
                         f"{path} was left unchanged (see the lines above)")
    return repos, lanes


# --- CLI ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Detect the repositories of a workspace",
                                 allow_abbrev=False)
    ap.add_argument("--root", default=".", help="workspace root to scan")
    ap.add_argument("--json", action="store_true", help="print the scan as JSON")
    ap.add_argument("--table", help="JSON list of the User-corrected rows to write instead of the scan")
    ap.add_argument("--write", metavar="FILE", help="pipeline.json to write repos{} and lanes{} into")
    args = ap.parse_args()

    try:
        rows = (json.loads(Path(args.table).read_text(encoding="utf-8")) if args.table
                else scan(Path(args.root)))
    except (OSError, ValueError) as exc:
        print(f"repo_scan: cannot read the table: {exc}", file=sys.stderr)
        return 2
    if not isinstance(rows, list):
        print("repo_scan: the table must be a JSON list of rows", file=sys.stderr)
        return 2

    if not args.write:
        print(json.dumps(rows, indent=2) if args.json else render(rows))
        return 0
    try:
        repos, lanes = write(Path(args.write), rows)
    except ValueError as exc:
        print(f"repo_scan: {exc}", file=sys.stderr)
        return 2
    keys = [k for k in repos if not k.startswith("_")]
    print(f"wrote repos{{}}: {', '.join(keys)}")
    print("wrote lanes{}: " + ", ".join(f"{k} -> {v['repo']}" for k, v in lanes.items()
                                        if not k.startswith("_")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

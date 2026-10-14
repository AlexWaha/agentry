#!/usr/bin/env python3
"""One git worktree per task: the single owner of worktree facts (epic-0004).

With `worktrees.enabled` in pipeline.json, registering a task cuts its branch and
checks it out in its own directory, `<repo path>-wt/<task>` beside the repo
(`src-wt/task-0130` for the repo `src`). The repo itself stays the TRUNK checkout.
Everything that creates, records, finds, uses or removes such a tree goes through
this module, so no other file spells the layout.

Two rules shape it. The RECORDED path (the run row's `worktree` column) drives every
behaviour; the flag only governs creation, so turning it off is a safe rollback. And
a recorded tree that is missing or malformed FAILS CLOSED: callers block, they never
fall back to the trunk checkout.

Every read function is subprocess-free (the hooks that call them have a time budget).
Most answer the safe value stated in their docstring and never raise. The three that
answer "who owns this path", "what is in flight" and "how many slots are used" (owner_of,
live, slots_used, orphans) raise Doubt instead of answering "nobody" / "none" when the
run store cannot be read or a run's tree cannot be located: callers take the fail-closed
side. Only add(), remove(), discard(), repair() and the CLI run git.

CLI (JSON on stdout):
    python worktree.py --list
    python worktree.py --sweep                  # reports orphans, removes nothing
    python worktree.py --remove --task T        # exit 1 when the tree had to be left
    python worktree.py --repair --task T
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import git_state
import state

STATUS_OK = "OK"
STATUS_LEGACY = "LEGACY"
STATUS_MISSING = "MISSING"
STATUS_INVALID = "INVALID"
SUFFIX = "-wt"

GIT_TIMEOUT = 30
ADD_TIMEOUT = 180
REMOVE_TRIES = 3
REMOVE_PAUSE = 1.0
READ_RETRY_PAUSE = 0.2
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

TASK_RE = re.compile(r"task-[0-9]+")
# The branch is `<type>/<task>`: a plain word, so the type cannot smuggle a path
# segment, an option or a ref operator into the git command.
TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
SHA_RE = re.compile(r"[0-9a-f]{40}")
CONTROL_RE = re.compile(r"[\x00-\x1f]")
# `\\?\E:\x`, `\\.\E:\x` and `//?/E:/x` are LOCAL spellings of `E:\x`.
EXTENDED_RE = re.compile(r"^[\\/]{2}[?.][\\/](?=[A-Za-z]:)")
# The branch name a dispatch line may print: a conservative charset on top of the work-branch
# shape, so a name an agent chose in a tree cannot put prompt text in front of the next agent.
SHOWN_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]+")


class Doubt(Exception):
    """The worktree facts cannot be read or trusted: the run store is unreadable, a run
    records a tree path that is not valid, a path cannot be placed. A reader that must
    answer "who owns this" or "how many slots are used" raises this instead of answering
    "nobody" / "none", so every caller can take the fail-closed side (frozen, full,
    refuse). It is an Exception, so a caller's `except Exception` already does."""


# --- config (contract C1) ----------------------------------------------------------

def _config() -> dict:
    block = state.load_pipeline().get("worktrees")
    return block if isinstance(block, dict) else {}


def enabled() -> bool:
    """True only for a literal `true`: a string, a number or a missing block is off."""
    try:
        return _config().get("enabled") is True
    except Exception:
        return False


def max_parallel() -> int:
    """How many tasks may hold a worktree at once; 1 for anything but an int >= 1."""
    try:
        value = _config().get("max_parallel")
    except Exception:
        return 1
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


# --- paths -------------------------------------------------------------------------

def _key(text: str) -> str:
    """A path spelling made comparable: forward slashes, no trailing slash, and
    case-folded on Windows, where the file system ignores case."""
    out = str(text).replace("\\", "/").rstrip("/")
    return out.casefold() if os.name == "nt" else out


def _repo_rels() -> set[str]:
    """Comparable workspace-relative paths of the known repos. The workspace root is
    left out: a repo that IS the root has no sibling directory to hold a tree."""
    out: set[str] = set()
    for repo in git_state.repos():
        try:
            rel = Path(repo).relative_to(state.ROOT).as_posix()
        except ValueError:
            continue
        if rel not in ("", "."):
            out.add(_key(rel))
    return out


def _valid(rel: str, task: str, rels: set[str]) -> bool:
    """valid_path against an already loaded `_repo_rels()`, so a caller that checks
    several rows reads the repo list once."""
    try:
        if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or CONTROL_RE.search(rel):
            return False
        parts = rel.split("/")
        if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
            return False
        leaf = parts[-1]
        if not TASK_RE.fullmatch(leaf) or (task and leaf != task):
            return False
        prefix = "/".join(parts[:-1])
        if not prefix.endswith(SUFFIX):
            return False
        return _key(prefix[:-len(SUFFIX)]) in rels
    except Exception:
        return False


def valid_path(rel: str, task: str = "") -> bool:
    """Shape check only, no file system: `<repo path>-wt/<task-NNNN>`, posix and
    relative, where `<repo path>` is the relative path of a known repo. When `task`
    is given the last segment must be that task."""
    try:
        return _valid(rel, task, _repo_rels())
    except Exception:
        return False


def repo_dir(rel: str) -> Path | None:
    """The repo a recorded tree belongs to (`src` for `src-wt/task-0130`); None when
    `rel` is not a valid tree path."""
    if not valid_path(rel):
        return None
    return state.ROOT / rel.rsplit("/", 1)[0][:-len(SUFFIX)]


def _wt_root(repo: Path) -> Path | None:
    """`<repo path>-wt` for a repo inside the workspace and not the workspace root."""
    try:
        rel = Path(repo).relative_to(state.ROOT).as_posix()
    except ValueError:
        return None
    if rel in ("", "."):
        return None
    return state.ROOT / f"{rel}{SUFFIX}"


def root_dir(repo_key: str = "") -> Path | None:
    """The absolute `<repo path>-wt` directory of the repo named by `repo_key` (a
    repos{} key; "" means the only repo). None when no repo or several resolve, or
    the repo is the workspace root. Creates nothing."""
    try:
        if repo_key:
            found = git_state.repo_path(repo_key)
            repos = [found] if found else []
        else:
            repos = git_state.repos()
        return _wt_root(repos[0]) if len(repos) == 1 else None
    except Exception:
        return None


# --- the run row -------------------------------------------------------------------

_COLUMNS = "task, type, stage, stage_status, awaiting_human, umbrella, worktree"


def _rows(where: str = "", params: tuple = ()) -> list[dict] | None:
    """Run rows, or None when the store cannot be read (callers decide whether that
    fails open or closed). A failed read is tried once more after a short pause: the
    store is shared with every hook and a writer holding it for a moment is not a
    reason to freeze, block or refuse."""
    for attempt in (0, 1):
        try:
            conn = state.connect()
            try:
                return [dict(r) for r in conn.execute(f"SELECT {_COLUMNS} FROM runs{where}", params)]
            finally:
                conn.close()
        except Exception:
            if attempt == 0:
                time.sleep(READ_RETRY_PAUSE)
    return None


def _need_rows(where: str = "", params: tuple = ()) -> list[dict]:
    """_rows, raising Doubt for a store that cannot be read."""
    rows = _rows(where, params)
    if rows is None:
        raise Doubt("the run store cannot be read")
    return rows


def recorded(task: str) -> str:
    """The relative posix path recorded on the task's run row; "" when none, no row
    or the store is unreadable."""
    rows = _rows(" WHERE task = ?", (task,))
    return str((rows[0].get("worktree") if rows else "") or "")


def _gitdir(tree: Path) -> Path | None:
    """The administrative directory a linked tree's `.git` file points at."""
    try:
        for line in (tree / ".git").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("gitdir:"):
                target = Path(line.split(":", 1)[1].strip())
                return target if target.is_absolute() else tree / target
    except OSError:
        pass
    return None


def _is_link(path) -> bool:
    """True for a symlink or a Windows reparse point (junction, mount point): a
    directory that stands for another one."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _plain_tree(tree: Path, rel: str) -> bool:
    """True when `tree` really is `rel` under the real workspace root: not a link
    itself, and no link on the way to it. A tree that fails this is somewhere else, and
    nothing may be reported usable, read, or deleted through it."""
    try:
        if _is_link(tree):
            return False
        want = os.path.join(os.path.realpath(state.ROOT), *rel.split("/"))
        return os.path.normcase(os.path.realpath(tree)) == os.path.normcase(want)
    except (OSError, ValueError):
        return False


def _tree_status(rel: str, task: str, rels: set[str] | None = None) -> str:
    if not rel:
        return STATUS_LEGACY
    if not _valid(rel, task, _repo_rels() if rels is None else rels):
        return STATUS_INVALID
    tree = state.ROOT / rel
    try:
        if not tree.is_dir():
            return STATUS_MISSING
        if not _plain_tree(tree, rel):
            return STATUS_INVALID
        if not (tree / ".git").is_file():
            return STATUS_MISSING
        admin = _gitdir(tree)
        return STATUS_OK if admin is not None and admin.is_dir() else STATUS_MISSING
    except OSError:
        return STATUS_MISSING


def status(task: str, strict: bool = False) -> str:
    """LEGACY: the run has no recorded tree (or there is no run). OK: recorded, well
    formed, a real directory (not a link, nothing linked on the way) whose `.git` link
    resolves. MISSING: recorded and well formed but the directory or its link is gone.
    INVALID: recorded but malformed or not the real directory, or run.db unreadable
    (fail closed). `strict` raises Doubt for an unreadable store instead of answering
    INVALID, for the caller that must tell "cannot read" from "bad". Pure file reads."""
    rows = _rows(" WHERE task = ?", (task,))
    if rows is None:
        if strict:
            raise Doubt("the run store cannot be read")
        return STATUS_INVALID
    try:
        return _tree_status(str((rows[0].get("worktree") if rows else "") or ""), task)
    except Exception:
        return STATUS_INVALID


def work_dir(task: str) -> Path | None:
    """The absolute tree when status is OK, else None. Callers that must tell
    LEGACY from MISSING and INVALID call status()."""
    rel = recorded(task)
    if rel and status(task) == STATUS_OK:
        return state.ROOT / rel
    return None


def _branch_of(tree: Path) -> str:
    """The branch a linked tree has checked out, read from its HEAD file ("" when
    detached or unreadable)."""
    admin = _gitdir(tree)
    try:
        head = (admin / "HEAD").read_text(encoding="utf-8", errors="replace").strip() if admin else ""
    except OSError:
        return ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


# --- locating a path ---------------------------------------------------------------

def _strip_streams(path: str) -> str:
    """`path` without NTFS alternate-data-stream suffixes (`dir::$DATA`), keeping the
    drive colon. Same rule as pretool_gate.strip_streams, which this module cannot
    import (pretool_gate imports this one)."""
    p = path.replace("\\", "/")
    drive = ""
    if re.match(r"^[A-Za-z]:", p):
        drive, p = p[:2], p[2:]
    return drive + "/".join(part.split(":", 1)[0] for part in p.split("/"))


def _real(path, cwd: str = "") -> str | None:
    """The real, comparable location of `path`; None on any doubt (empty, UNC)."""
    try:
        # The prefix goes first: _strip_streams would eat the drive colon behind it.
        raw = _strip_streams(EXTENDED_RE.sub("", str(path).strip().strip("'\"")))
        raw = re.sub(r"^/([A-Za-z])(?=/|$)", lambda m: m.group(1).upper() + ":", raw)
        if not raw or re.match(r"^[\\/]{2,}[^\\/]", raw):
            return None
        base = cwd if cwd and os.path.isabs(cwd) else str(state.ROOT)
        return os.path.normcase(os.path.realpath(os.path.join(base, raw)))
    except (OSError, ValueError):
        return None


def _inside(target: str, directory: str) -> bool:
    return target == directory or target.startswith(directory.rstrip("\\/") + os.sep)


def owner_of(path, cwd: str = "") -> str | None:
    """The task whose RECORDED tree contains `path` (a relative `path` resolves against
    `cwd`, else the workspace root). None means the whole store was read and `path`
    lies in no recorded tree: the trunk checkout, an unrecorded directory, anything
    outside. Raises Doubt when that cannot be said: the store is unreadable, `path`
    cannot be placed (empty, UNC), or a run still in flight records a tree path that is
    not valid, so the tree it really has could hold `path`. Only runs not yet done are
    read; the leftover of a finished run is nobody's."""
    target = _real(path, cwd)
    if target is None:
        raise Doubt(f"cannot place {str(path)!r}")
    rows = _need_rows(" WHERE worktree != '' AND COALESCE(stage, '') != 'done'")
    try:
        rels = _repo_rels()
        trees: list[tuple[str, str]] = []
        for row in rows:
            task, rel = str(row["task"]), str(row.get("worktree") or "")
            tree = _real(state.ROOT / rel) if _valid(rel, task, rels) else None
            if tree is None:
                raise Doubt(f"{task} records the worktree {rel!r}, which is not a valid path")
            trees.append((task, tree))
    except Doubt:
        raise
    except Exception as exc:
        raise Doubt(f"{type(exc).__name__}: {exc}") from exc
    return next((task for task, tree in trees if _inside(target, tree)), None)


def is_trunk_checkout(path, cwd: str = "") -> bool:
    """True when `path` lies inside a repo whose `.git` is a directory and whose HEAD
    is the main branch: the checkout nobody edits when worktrees are on. A repo on a
    feature branch (a LEGACY run or a swarm umbrella holds it) is not trunk, so it
    stays editable. Reads `.git/HEAD` only; independent of the flag; False on doubt."""
    target = _real(path, cwd)
    if target is None:
        return False
    try:
        best: Path | None = None
        best_dir = ""
        for repo in git_state.repos():
            here = _real(repo)
            if here and _inside(target, here) and len(here) > len(best_dir):
                best, best_dir = Path(repo), here
        if best is None or not (best / ".git").is_dir():
            return False
        head = (best / ".git" / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        return head == f"ref: refs/heads/{git_state.main_branch()}"
    except Exception:
        return False


def in_checkout(path, cwd: str = "") -> bool:
    """True when `path` lies inside a repo's own checkout (a repos{} entry other than the
    workspace root): the trunk, or the repo of a run that has no tree. A task's tree is
    `<repo>-wt/<task>`, beside the repo, so it is not inside it. Independent of the flag.
    Raises Doubt when `path` cannot be placed."""
    target = _real(path, cwd)
    if target is None:
        raise Doubt(f"cannot place {str(path)!r}")
    for repo in git_state.repos():
        try:
            rel = Path(repo).relative_to(state.ROOT).as_posix()
        except ValueError:
            continue
        here = _real(repo) if rel not in ("", ".") else None
        if here and _inside(target, here):
            return True
    return False


# --- what is in flight -------------------------------------------------------------

def live() -> list[dict]:
    """One dict per in-flight run (not done, not paused, not abandoned), by task id.
    A row whose recorded tree is invalid is listed with status INVALID. Raises Doubt
    when the store cannot be read: an empty list would read as "nothing in flight"."""
    rows = _need_rows()
    out: list[dict] = []
    try:
        rels = _repo_rels()
        for row in sorted(rows, key=lambda r: str(r["task"])):
            if (row.get("stage") or "") == "done" or row.get("stage_status") in (
                    state.ST_PAUSED, state.ST_ABANDONED):
                continue
            rel = str(row.get("worktree") or "")
            out.append({
                "task": str(row["task"]), "stage": str(row.get("stage") or ""),
                "stage_status": str(row.get("stage_status") or ""),
                "awaiting_human": str(row.get("awaiting_human") or ""),
                "umbrella": row.get("umbrella") == "true", "worktree": rel,
                "status": _tree_status(rel, str(row["task"]), rels),
            })
    except Exception as exc:
        raise Doubt(f"{type(exc).__name__}: {exc}") from exc
    return out


def slots_used() -> int:
    """Worktrees held by in-flight, non-umbrella runs. A parked or blocked run keeps
    its slot; a paused one gives it up. Raises Doubt (from live) for an unreadable
    store: 0 would read as "every slot is free"."""
    return len([r for r in live() if r["worktree"] and not r["umbrella"]])


def orphans() -> list[dict]:
    """Directories `<repo>-wt/task-*` that no run in flight or paused records:
    {"path", "task", "why"}, by path. Reports only. Raises Doubt for an unreadable
    store, which would otherwise call every tree an orphan."""
    rows = _need_rows()
    try:
        keep: dict[str, str] = {}
        for row in rows:
            if row.get("worktree") and (row.get("stage") or "") != "done":
                keep[_key(str(row["worktree"]))] = str(row["task"])
        by_task = {str(r["task"]): r for r in rows}
        found: list[dict] = []
        for repo in git_state.repos():
            root = _wt_root(repo)
            if root is None or not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not (child.is_dir() and TASK_RE.fullmatch(child.name)):
                    continue
                rel = child.relative_to(state.ROOT).as_posix()
                if _key(rel) in keep:
                    continue
                row = by_task.get(child.name)
                if row is None:
                    why = "no run"
                elif row.get("stage_status") == state.ST_ABANDONED:
                    why = "run abandoned"
                elif (row.get("stage") or "") == "done":
                    why = "run done"
                else:
                    why = "not recorded by its run"
                found.append({"path": rel, "task": child.name, "why": why})
        return sorted(found, key=lambda o: o["path"])
    except Exception:
        return []


# --- messages ----------------------------------------------------------------------

def stale_message(n: int) -> str:
    """The single freshness text: the trunk moved on after the branch was cut."""
    return f"main moved {n} commits: `git merge main` in the worktree"


def _shown_branch(tree: Path) -> str:
    """The branch a tree has checked out, for the dispatch line: only a name of the work-branch
    shape in a plain charset, else "detached". An agent can check out any branch in its tree,
    and this text goes into the prompt of the next one."""
    name = _branch_of(tree)
    try:
        from pretool_gate import valid_work_branch  # lazy: pretool_gate imports this module
        shown = bool(SHOWN_BRANCH_RE.fullmatch(name)) and valid_work_branch(name)
    except Exception:
        shown = False
    return name if shown else "detached"


def identity_line(task: str) -> str:
    """`Task worktree: <task>`: the line a dispatch prompt for this task opens with, alone
    on its line. agent_gate reads it from the agent's own transcript, the way it reads
    `Swarm zone:`. "" unless the task has a usable tree."""
    try:
        return f"Task worktree: {task}" if status(task) == STATUS_OK else ""
    except Exception:
        return ""


def holds_tree(task: str) -> bool:
    """True when `task` has a usable tree (status OK) and its run sits at a stage an agent
    edits in: the only state in which a `Task worktree: <task>` line is believed. Raises
    Doubt when the run store cannot be read."""
    if status(task, strict=True) != STATUS_OK:
        return False
    rows = _need_rows(" WHERE task = ?", (task,))
    return bool(rows) and str(rows[0].get("stage") or "") in state.editing_stages(state.load_pipeline())


def dispatch_line(task: str) -> str:
    """The one line every implement, test and review dispatch carries. Byte-stable: no
    clock, no counters. "" for a run with no tree of its own. The usable form starts with
    the label `Worktree:` and holds the identity text only mid-line, so it can never read as
    a second `Task worktree:` value next to the line the orchestrator puts on top."""
    try:
        st = status(task)
        if st == STATUS_LEGACY:
            return ""
        if st != STATUS_OK:
            return (f"Task worktree: {st} for {task} - run python "
                    f"\"$AGENTRY_ROOT/tools/pipeline/worktree.py\" --repair --task {task}; do not edit.")
        rel = recorded(task)
        tree = (state.ROOT / rel).as_posix()
        repo = rel.rsplit("/", 1)[0][:-len(SUFFIX)]
        return (f"Worktree: {tree} (branch {_shown_branch(state.ROOT / rel)}). "
                f'Begin every Bash call with cd "{tree}", give Edit and Write absolute paths '
                f"under it, and do not touch {repo}/. Make the first line of the dispatch prompt "
                f'exactly "{identity_line(task)}" (alone on that line), or the write gate '
                f"refuses this agent's writes.")
    except Exception:
        return ""


# --- git ---------------------------------------------------------------------------

def _git(repo, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """(exit code, stdout and stderr). git's own messages decide the removal outcome
    below, so they are read in the C locale."""
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C", "LANGUAGE": "C"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _registered(repo) -> dict[str, str]:
    """Comparable tree path -> branch ("" when detached) for every worktree the repo
    knows, the main checkout included."""
    code, out = _git(repo, "worktree", "list", "--porcelain")
    trees: dict[str, str] = {}
    here = None
    for line in out.splitlines() if code == 0 else []:
        if line.startswith("worktree "):
            here = _key(line[len("worktree "):])
            trees[here] = ""
        elif line.startswith("branch refs/heads/") and here is not None:
            trees[here] = line[len("branch refs/heads/"):]
    return trees


def _refuse(error: str, manual: str = "") -> dict:
    return {"ok": False, "error": error, "manual": manual}


def add(task: str, task_type: str, repo: str = "") -> dict:
    """Cut `<type>/<task>` from the trunk into `<repo path>-wt/<task>`.

    `repo` is the task's declared `repo:` value. Returns {"ok": True, "path", "abs",
    "branch", "base", "created"} or {"ok": False, "error", "manual"}; never raises.
    An existing tree is reused only when it is exactly this task's tree on exactly
    this branch (created False); anything else is refused with the command a human
    can run."""
    try:
        return _add(task, task_type, repo)
    except Exception as exc:
        return _refuse(f"worktree add failed: {type(exc).__name__}: {exc}")


def _add(task: str, task_type: str, declared: str) -> dict:
    if not TASK_RE.fullmatch(task or ""):
        return _refuse(f"{task!r} is not a task id (task-NNNN)")
    if not TYPE_RE.fullmatch(task_type or ""):
        return _refuse(f"task type {task_type!r} cannot name a branch (letters, digits, "
                       f"underscore, hyphen)")
    from pretool_gate import BRANCH_TYPES  # lazy: pretool_gate imports this module
    if task_type not in BRANCH_TYPES:
        # The commit gate only lets a branch named <work type>/...task-NNNN take commits.
        return _refuse(f"task type {task_type!r} is not a branch type the gates accept "
                       f"({', '.join(BRANCH_TYPES)}); register the task with --type set to one")
    repos = git_state.repos_named(declared) if declared else git_state.repos()
    if len(repos) != 1:
        return _refuse(
            f"cannot tell which repo task {task} lives in: {len(repos)} candidates"
            + (f" for repo: '{declared}'" if declared else "")
            + " - name exactly one with a repo: line in the task file")
    repo = Path(repos[0])
    root = _wt_root(repo)
    if root is None:
        return _refuse(f"repo {repo} is outside the workspace or is the workspace root, so "
                       f"it has no sibling directory for worktrees")
    rel = f"{root.relative_to(state.ROOT).as_posix()}/{task}"
    if not valid_path(rel, task):
        return _refuse(f"{rel} is not a valid worktree path")
    tree = state.ROOT / rel
    branch = f"{task_type}/{task}"
    trunk = git_state.trunk(repo)
    code, base = _git(repo, "rev-parse", "--verify", "--quiet", f"{trunk}^{{commit}}")
    if code != 0 or not SHA_RE.fullmatch(base):
        return _refuse(f"cannot resolve the trunk '{trunk}' in {repo}")

    known = _registered(repo).get(_key(tree.as_posix()))
    if known is not None:
        if known == branch and (tree / ".git").is_file():
            return _ok(rel, tree, branch, base, created=False)
        if known == branch:
            # Only this tree's stale entry goes; a bare `worktree prune` would also drop
            # the entry of any other tree that is missing for a moment.
            return _refuse(f"{rel} is registered with git but its directory is gone",
                           f"git -C {shlex.quote(repo.as_posix())} worktree remove "
                           f"{shlex.quote(tree.as_posix())}")
        return _refuse(f"{rel} already exists on {known or 'a detached HEAD'}, not on {branch}",
                       f"git -C {shlex.quote(repo.as_posix())} worktree list")
    if tree.exists():
        return _refuse(f"{rel} already exists and is not a worktree of {repo.name}; move it "
                       f"away and register again")
    code, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if code == 0:
        return _refuse(f"branch {branch} already exists in {repo.name} without a tree at {rel}",
                       f"git -C {shlex.quote(repo.as_posix())} worktree add "
                       f"{shlex.quote(tree.as_posix())} {shlex.quote(branch)}")

    code, out = _git(repo, "worktree", "add", "--no-track", "-b", branch, str(tree), trunk,
                     timeout=ADD_TIMEOUT)
    if code != 0:
        return _refuse(f"git worktree add failed: {out.splitlines()[-1][:300] if out else code}")
    head_code, head = _git(tree, "rev-parse", "HEAD")
    name_code, name = _git(tree, "rev-parse", "--abbrev-ref", "HEAD")
    if head_code != 0 or name_code != 0 or head != base or name != branch:
        _drop(_ok(rel, tree, branch, base, created=True))
        return _refuse(f"{rel} came out on '{name}' at {head[:12]}, expected '{branch}' at "
                       f"{base[:12]}; the tree was removed")
    (tree / "tmp").mkdir(exist_ok=True)
    return _ok(rel, tree, branch, base, created=True)


def _ok(rel: str, tree: Path, branch: str, base: str, created: bool) -> dict:
    return {"ok": True, "path": rel, "abs": tree.as_posix(), "branch": branch, "base": base,
            "created": created}


def _clear_readonly(func, path, exc) -> None:
    """rmtree's error hook: clear the read-only bit and retry the one operation."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc from None


def discard(result: dict) -> None:
    """Undo a registration that created a tree but never got a run row (the insert
    failed). A racing registration of the same task may have reused this very tree and
    recorded it, so the run rows are read first: a recorded tree is that run's now, and
    an unreadable store proves nothing, so in both cases it stays (the next registration
    reuses it). Never raises."""
    try:
        rel = str(result.get("path") or "")
        rows = _rows(" WHERE worktree != ''")
        if rows is None or any(_key(str(r["worktree"])) == _key(rel) for r in rows):
            return
        _drop(result)
    except Exception:
        pass


def _drop(result: dict) -> None:
    """Remove the tree a registration just created (forced, it holds nothing of
    anyone's) and the branch it created, the branch only while it still sits on the
    commit it was cut at."""
    rel = str(result.get("path") or "")
    repo = repo_dir(rel)
    if repo is None or not result.get("created"):
        return
    tree = state.ROOT / rel
    if os.path.lexists(tree) and not _plain_tree(tree, rel):
        return
    _git(repo, "worktree", "remove", "--force", str(result["abs"]))
    code, tip = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{result['branch']}")
    if code == 0 and tip == result.get("base"):
        _git(repo, "branch", "-D", str(result["branch"]))


def remove(task: str) -> dict:
    """Remove the task's tree with a plain `git worktree remove` (never --force).

    Returns {"task", "removed", "left", "reason"}: removed True when the tree is gone;
    left True when it remains (reason says why and what to do: dirty, locked, in use,
    refused); both False with an empty reason when there was nothing to remove. The
    branch is never deleted and the run row never touched. Never raises.

    A dirty or locked tree is refused before anything is deleted, so it is not
    retried. A tree held open by another process is different: on Windows git deletes
    its own bookkeeping first and fails on the directory, which leaves a clean but
    unregistered leftover that git will never touch again (measured, task-0133 M2).
    Only within the call that saw that failure is the leftover deleted directly.

    Nothing is deleted through a link: a tree that is one, or has one on its path, is
    left; links INSIDE the tree are taken out first, because git follows a junction in
    an ignored directory and deletes its target's files (measured, task-0133 M2b)."""
    out = {"task": task, "removed": False, "left": False, "reason": ""}
    try:
        rows = _rows(" WHERE task = ?", (task,))
        if rows is None:
            return {**out, "left": True, "reason": (
                f"the run store cannot be read, so nothing was removed; run again: {_hint(task)}")}
        rel = str((rows[0].get("worktree") if rows else "") or "")
        if not rel:
            return out
        repo = repo_dir(rel)
        if repo is None or not valid_path(rel, task):
            return {**out, "left": True, "reason": f"recorded path '{rel}' is not a valid worktree path"}
        tree = state.ROOT / rel
        if not os.path.lexists(tree):
            # Drops this tree's stale registration and nothing else: a bare
            # `git worktree prune` would also drop that of any tree missing for a moment.
            _git(repo, "worktree", "remove", str(tree))
            return out
        if not tree.is_dir() or not _plain_tree(tree, rel):
            return {**out, "left": True, "reason": (
                f"{rel} is not a plain directory (it is, or sits behind, a link), so it was "
                f"not touched; check it and delete it by hand")}
        _step_out_of(tree)
        _detach_links(tree)
        return _remove_tree(out, repo, tree, rel)
    except Exception as exc:
        return {**out, "removed": False, "left": True, "reason": f"{type(exc).__name__}: {exc}"}


def _hint(task: str) -> str:
    return f"python \"$AGENTRY_ROOT/tools/pipeline/worktree.py\" --remove --task {task}"


def _step_out_of(tree: Path) -> None:
    """A process cannot delete the directory it is standing in (measured: the same
    refusal as a child's cwd), and an orchestrator that `cd`ed into the tree to run
    advance.py is exactly that. Move this process to the workspace root first."""
    here, there = _real(os.getcwd()), _real(tree)
    if here and there and _inside(here, there):
        os.chdir(state.ROOT)


def _detach_links(tree: Path) -> None:
    """Take every directory link (junction, symlink) out of the tree, the link only and
    never its target, and do not walk into it. os.walk descends into a junction."""
    for here, dirs, _files in os.walk(tree):
        for name in list(dirs):
            link = os.path.join(here, name)
            if _is_link(link):
                dirs.remove(name)
                try:
                    os.rmdir(link)
                except OSError:
                    os.unlink(link)


def _remove_tree(out: dict, repo: Path, tree: Path, rel: str) -> dict:
    hint = _hint(out["task"])
    by_hand = f"rm -rf {shlex.quote(tree.as_posix())}"

    def in_use(detail: str) -> str:
        return (f"in use: {detail}. git already unregistered {rel}, so --remove cannot finish "
                f"it: close whatever holds it (a shell or program with that directory open), "
                f"then delete it by hand: {by_hand}")

    partial = False
    reason = ""
    for attempt in range(REMOVE_TRIES):
        if attempt:
            time.sleep(REMOVE_PAUSE)
        if partial:
            if not _plain_tree(tree, rel):
                return {**out, "left": True, "reason": (
                    f"{rel} stopped being a plain directory while it was being removed, so it "
                    f"was not touched further; check it and delete it by hand")}
            try:
                shutil.rmtree(tree, onexc=_clear_readonly)
            except OSError as exc:
                reason = in_use(str(exc).splitlines()[0][:200])
            if not tree.exists():
                return {**out, "removed": True}
            continue
        code, text = _git(repo, "worktree", "remove", str(tree))
        low = text.lower()
        if code == 0:
            return {**out, "removed": True}
        if "modified or untracked" in low:
            return {**out, "left": True, "reason": (
                f"dirty: modified or untracked files in {rel}; commit or discard them there, "
                f"then run: {hint}")}
        if "locked working tree" in low:
            return {**out, "left": True, "reason": (
                f"locked; unlock it with: git -C {shlex.quote(repo.as_posix())} worktree unlock "
                f"{shlex.quote(tree.as_posix())} - then run: {hint}")}
        if "not a working tree" in low:
            return {**out, "left": True, "reason": (
                f"not registered with git (an earlier removal was interrupted): close whatever "
                f"holds {rel}, then delete the directory by hand: {by_hand}")}
        if "failed to delete" in low:
            partial = True
            reason = in_use(text.splitlines()[-1][:200])
            continue
        reason = (f"{text.splitlines()[-1][:200]}; fix that, then run: {hint}" if text
                  else f"git exited {code}; run: {hint}")
    return {**out, "left": True, "reason": reason}


def repair(task: str) -> dict:
    """Reconnect a task's recorded tree: `git worktree repair` when the directory is
    there, `git worktree add <path> <branch>` when it is gone and the branch exists.
    {"task", "ok", "message"}; never raises."""
    try:
        rel = recorded(task)
        repo = repo_dir(rel)
        if not rel or repo is None or not valid_path(rel, task):
            return {"task": task, "ok": False, "message": f"no valid recorded worktree for {task}"}
        tree = state.ROOT / rel
        if os.path.lexists(tree) and not (tree.is_dir() and _plain_tree(tree, rel)):
            return {"task": task, "ok": False, "message": (
                f"{rel} is not a plain directory (it is, or sits behind, a link); "
                f"nothing was touched")}
        if tree.is_dir():
            code, text = _git(repo, "worktree", "repair", str(tree))
        else:
            # git still lists the vanished directory as registered and refuses to add
            # over it until that one stale entry is dropped (not a bare prune: that
            # would drop the entry of any other tree missing for a moment).
            _git(repo, "worktree", "remove", str(tree))
            rows = _rows(" WHERE task = ?", (task,)) or [{}]
            branch = f"{rows[0].get('type') or 'feature'}/{task}"
            code, text = _git(repo, "worktree", "add", str(tree), branch, timeout=ADD_TIMEOUT)
        # The outcome is the tree's state, not git's exit code: `worktree repair` exits
        # non-zero after it has fixed a link it first had to complain about.
        after = status(task)
        if after == STATUS_OK:
            return {"task": task, "ok": True, "message": f"{rel} is linked and usable"}
        return {"task": task, "ok": False,
                "message": f"{rel} is {after} after repair (git exited {code}): "
                           f"{text[:300] or 'no output'}"}
    except Exception as exc:
        return {"task": task, "ok": False, "message": f"{type(exc).__name__}: {exc}"}


# --- CLI ---------------------------------------------------------------------------

def _listing() -> dict:
    trees = []
    for row in live():
        if row["worktree"]:
            trees.append({"task": row["task"], "path": row["worktree"],
                          "branch": (_branch_of(state.ROOT / row["worktree"])
                                     if row["status"] == STATUS_OK else ""),
                          "status": row["status"], "stage": row["stage"]})
    return {"enabled": enabled(), "max_parallel": max_parallel(), "trees": trees}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Task worktrees")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="in-flight trees and their status")
    group.add_argument("--sweep", action="store_true", help="report orphaned trees; removes nothing")
    group.add_argument("--remove", action="store_true", help="remove --task's tree (no --force)")
    group.add_argument("--repair", action="store_true", help="reconnect --task's tree")
    ap.add_argument("--task")
    args = ap.parse_args(argv)
    if (args.remove or args.repair) and not (args.task and TASK_RE.fullmatch(args.task)):
        print(json.dumps({"error": "--remove and --repair need --task task-NNNN"}))
        return 2
    try:
        if args.list:
            print(json.dumps(_listing(), indent=2))
            return 0
        if args.sweep:
            print(json.dumps({"orphans": orphans()}, indent=2))
            return 0
    except Doubt as exc:
        print(json.dumps({"error": f"cannot tell: {exc}"}))
        return 1
    if args.remove:
        result = remove(args.task)
        print(json.dumps(result, indent=2))
        return 1 if result["left"] else 0
    result = repair(args.task)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

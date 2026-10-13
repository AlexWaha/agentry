#!/usr/bin/env python3
"""Ground truth about branches, read from git rather than from our own records.

The run store knows what the agent DID: registered a task, ran a gate, pushed a
branch. It has no idea what happened afterwards - whether the branch was merged,
rejected or simply forgotten. For a long time `done` therefore meant "pushed",
and a branch that never reached the main line still looked finished. One shipped
that way and nobody noticed for a day.

Everything here answers a question git can answer on its own, so a task is only
called done when the main branch actually contains it.

Every function fails open: on any git error the answer is None, meaning
"unknown", never a confident wrong answer.

CLI:
    python git_state.py --repo <path> --task task-1234
    python git_state.py --repo <path> --branch feature/task-1234
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import state

FETCH_TIMEOUT = 60
GIT_TIMEOUT = 20


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    try:
        # CREATE_NO_WINDOW: the supervisor daemon is started DETACHED, with no
        # console, so a console child would allocate a window of its own. Utf-8
        # because git writes paths in it whatever the Windows code page is.
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _warn(message: str) -> None:
    print(f"git_state: repos{{}} {message}", file=sys.stderr)


def _valid_entry(key: str, raw: object) -> dict | None:
    """One repos{} entry in contract C-3 shape, normalised, or None with the
    reason on stderr. A bad entry is dropped on its own, so one typo does not
    take the other repos down with it."""
    if not isinstance(raw, dict):
        _warn(f"entry '{key}' is not an object")
        return None
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        _warn(f"entry '{key}' needs a non-empty string path")
        return None
    # Workspace-relative and inside the workspace: a rooted or drive-qualified
    # value would make `ROOT / path` ignore ROOT, and `..` leaves it.
    if Path(path).anchor or ".." in Path(path).parts:
        _warn(f"entry '{key}' path '{path}' must stay inside the workspace")
        return None
    stack = raw.get("stack", "")
    stages = raw.get("stages", {})
    if not isinstance(stack, str) or not isinstance(stages, dict):
        _warn(f"entry '{key}' needs a string stack and an object stages")
        return None
    for stage, cmds in stages.items():
        if not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds):
            _warn(f"entry '{key}' stage '{stage}' must be a list of command strings")
            return None
    return {"path": path, "stack": stack, "stages": {s: list(c) for s, c in stages.items()}}


def repo_entries() -> dict[str, dict]:
    """The `repos{}` object of pipeline.json (contract C-3), validated.

    Maps repo key -> {"path", "stack", "stages"}; `stages` maps a stage name to
    its command list, and a stage that is absent or empty means "no gate here".
    Fails open to {}: an absent key and the legacy list form are silent (both
    are supported ways to have no object config), a malformed object or entry
    is reported on stderr. Keys starting with `_` are comments, as everywhere
    else in pipeline.json."""
    configured = state.load_pipeline().get("repos")
    if configured is None or isinstance(configured, list):
        return {}
    if not isinstance(configured, dict):
        _warn("must be an object")
        return {}
    out: dict[str, dict] = {}
    for key, raw in configured.items():
        if key.startswith("_"):
            continue
        entry = _valid_entry(key, raw)
        if entry:
            out[key] = entry
    return out


def repo_path(key: str) -> Path | None:
    """The working directory of a configured repo, or None for an unknown key."""
    entry = repo_entries().get(key)
    return state.ROOT / entry["path"] if entry else None


def repos() -> list[Path]:
    """Every git repo the pipeline works in: the `repos{}` entries when the
    config has them, the legacy list of workspace-relative paths otherwise.
    Without either, auto-detect: a workspace root that is not itself a repo
    holds them as subdirectories, so look one level down too."""
    entries = repo_entries()
    if entries:
        return [state.ROOT / e["path"] for e in entries.values()]
    legacy = state.load_pipeline().get("repos")
    if isinstance(legacy, list) and legacy:
        return [state.ROOT / str(r) for r in legacy]

    found: list[Path] = []
    if is_repo_root(state.ROOT):
        found.append(state.ROOT)
    for child in sorted(state.ROOT.iterdir()):
        try:
            if child.is_dir() and is_repo_root(child):
                found.append(child)
            elif child.is_dir():
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir() and is_repo_root(grandchild):
                        found.append(grandchild)
        except OSError:
            continue
    return found


def is_repo_root(path: Path) -> bool:
    """True when `path` holds a git repo of its own: a `.git` directory, or a `.git`
    file (a submodule checkout, a repo made with --separate-git-dir) that is not a
    LINKED WORKTREE's. A linked worktree's `.git` file points at
    `<common git dir>/worktrees/<name>`; it is a second checkout of a repo already
    listed, never a repo of its own (epic-0004), so `src-wt/task-0130` is never
    scanned, fetched or merge-checked as if it were one."""
    git = Path(path) / ".git"
    try:
        if git.is_dir():
            return True
        if not git.is_file():
            return False
        for line in git.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("gitdir:"):
                target = Path(line.split(":", 1)[1].strip())
                return target.parent.name.casefold() != "worktrees"
    except OSError:
        pass
    return False


def main_branch() -> str:
    return str(state.load_pipeline().get("main_branch") or "main")


def fetch(repo: Path) -> bool:
    code, _ = _git(repo, "fetch", "origin", main_branch(), timeout=FETCH_TIMEOUT)
    return code == 0


def _ref_exists(repo: Path, ref: str) -> bool:
    code, _ = _git(repo, "rev-parse", "--verify", "--quiet", ref)
    return code == 0


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    code, _ = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    return code == 0


def trunk(repo: Path) -> str:
    """The ref that stands for the main line in THIS repo.

    `origin/main` is the right answer only while a remote exists and is current.
    In `solo` workflow mode the task branch is merged into the trunk locally and
    the trunk is pushed later, or there is no remote at all - so origin/main is
    either behind or missing, and every task whose work sits on local main reads
    as unmerged forever. Measured: task-0003 sat at awaiting_human=merge with
    main genuinely carrying it.

    Local wins when it is not behind the remote, so the union of local and
    pushed history is what merge detection is compared against. When neither ref
    resolves, the remote name is returned unchanged: an unreadable repo must
    answer 'cannot tell', not 'merged'."""
    local, remote = main_branch(), f"origin/{main_branch()}"
    if not _ref_exists(repo, remote):
        return local
    if _ref_exists(repo, local) and _is_ancestor(repo, remote, local):
        return local
    return remote


def merged_into_main(repo: Path, ref: str) -> bool | None:
    """True only when the trunk carries WORK that arrived from ref.

    Containment of the branch POINTER is not that, and used to be what this
    answered. A branch freshly cut from main is trivially an ancestor of it
    while carrying nothing - the state every task starts in - so the old
    ancestry test said True in the window between `git checkout -b` and the
    first commit, and `done` could retire a task that shipped nothing.

    The positive evidence is the shape of a merge: a merged branch tip sits on
    the SIDE of a merge commit, off the trunk's first-parent chain, so the trunk
    reaching it proves both that the merge happened and that the merged side was
    non-empty. A tip still ON the first-parent chain is either an empty branch
    or a fast-forward merge, and those two are indistinguishable from the refs
    alone - that is None, cannot tell, which parks. Never True.

    Three outcomes, kept apart on purpose:
      True  - the trunk carries commits that came in from this branch
      False - the trunk does not contain the ref at all
      None  - cannot tell: missing ref, unreadable repo, or a tip on the
              first-parent chain (empty branch or fast-forward)."""
    if not _ref_exists(repo, ref):
        return None
    code, sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if code != 0 or not sha:
        return None
    main_ref = trunk(repo)
    if not _ref_exists(repo, main_ref):
        return None
    if not _is_ancestor(repo, ref, main_ref):
        return False
    code, chain = _git(repo, "rev-list", "--first-parent", main_ref)
    if code != 0:
        return None
    return None if sha in chain.splitlines() else True


def task_in_main(repo: Path, task: str) -> bool | None:
    """True when the trunk carries a commit naming this task id.

    Reads the commit log rather than the branch tip, because a squash merge
    leaves the branch itself outside main while its content is in.

    Three spellings count, because three of them occur in practice:

      [task-0004]                  the convention rules/git-workflow.md asks for,
                                   leading a line of the message (leading blanks
                                   allowed): the grammar of the commit tag the
                                   commit gate reads (pretool_gate.COMMIT_TAG_RE)
      task-0004:                   a conventional-commit subject
      ^Merge branch 'x/task-0004'  git's own default merge message (line start)

    The third is the one that carries most merges in `solo` mode, where the
    pipeline merges locally: a task whose commits carry no tag at all still
    leaves its id in the merge commit. Matching only the bracketed form made
    every other spelling read as unmerged, so a genuinely merged task parked
    forever.

    The bracketed arm is anchored to the start of a line too. Unanchored, any
    commit that merely MENTIONED `[task-0004]` mid-sentence ("revert the fix from
    [task-0004]") read as that task's own merge, which closed a run that had
    skipped its commit checkpoint.

    Both non-bracketed arms need a TERMINATOR, or the id is only a prefix and
    the wrong task closes:

      - the subject arm is anchored to the start of a line (`^`), not merely
        preceded by a non-word character. `[^-a-z]` matched the space in
        `hotfix for task-0004: ...` and the quote in `Revert "task-0004: ..."`,
        both of which are commits ABOUT the task, not the task's own subject.
      - the merge arm ends on a character that cannot continue the id
        (`[^0-9a-z]`, which the closing quote satisfies), so
        `Merge branch 'x/task-00041'` no longer closes task-0004.

    Anchored with -E throughout.

    Not ported: `fin.local`'s fourth arm, `sub-task-0004:`. It serves the
    one-branch-per-epic convention, which the FR-4 inventory flagged as needing
    a CEO decision and did NOT port - an arm matching a convention this template
    does not have would be dead on arrival."""
    code, out = _git(repo, "log", trunk(repo), "--oneline", "-E",
                     f"--grep=(^[[:space:]]*\\[{task}\\]|^{task}:"
                     f"|^Merge branch '[^']*{task}[^0-9a-z])", "-1")
    if code != 0:
        return None
    return bool(out)


def branch_for(repo: Path, task: str) -> str | None:
    """The branch carrying this task - the pushed copy when there is one, the
    local branch otherwise. A solo project never pushes a task branch, so
    remote-only resolution found nothing and the task looked branchless."""
    for args in (("-r", "--list", f"origin/*{task}*"), ("--list", f"*{task}*")):
        code, out = _git(repo, "branch", *args)
        if code != 0 or not out:
            continue
        # `git branch --list` marks the checked-out branch with '*' (or '+' for
        # a worktree); the marker is not part of the name.
        first = out.splitlines()[0].strip().lstrip("*+").strip()
        if first:
            return first
    return None


def is_pushed(repo: Path, ref: str | None) -> bool:
    """Whether a remote copy of this branch exists.

    branch_for now returns local branches too, so `ref.startswith("origin/")` is
    no longer the same question - it would call an unpushed branch pushed."""
    if not ref:
        return False
    if ref.startswith("origin/"):
        return True
    return _ref_exists(repo, f"origin/{ref}")


def declared_ref(repo: Path, branch: str) -> str | None:
    """The ref that really exists for a branch a task DECLARED it rides on
    (frontmatter `branch:`), preferring the pushed copy over the local one.

    Not every task gets a branch named after itself. Work that deliberately
    rides on a sibling's branch matches neither `<type>/task-NNNN` nor a
    `[task-NNNN]` commit, so without the declaration it looks like a task with
    no branch at all - which is exactly how one got called done while its code
    sat unmerged on a sibling's branch."""
    if not branch:
        return None
    for ref in (f"origin/{branch}", branch):
        code, _ = _git(repo, "rev-parse", "--verify", "--quiet", ref)
        if code == 0:
            return ref
    return None


def base_is_current(repo: Path, ref: str = "HEAD") -> bool | None:
    """True when the trunk is an ancestor of ref: the branch was cut from an
    up-to-date main and nothing has landed since that it lacks."""
    return _is_ancestor(repo, trunk(repo), ref)


def behind_trunk(repo: Path, ref: str = "HEAD", timeout: int | None = None) -> int | None:
    """How many commits the trunk has that `ref` lacks: 0 is fresh, None is "cannot
    tell" (a git error, an unresolvable ref). `repo` may be a linked worktree - the
    refs are shared, so the trunk reads the same from every checkout. `timeout`
    None means GIT_TIMEOUT."""
    limit = GIT_TIMEOUT if timeout is None else timeout
    code, out = _git(repo, "rev-list", "--count", f"{ref}..{trunk(repo)}", timeout=limit)
    return int(out) if code == 0 and out.isdigit() else None


def resolve_key(entries: dict[str, dict], repo: str) -> str:
    """The repos{} key for a `repo:` value, or "". Loose on purpose, because task
    files carry both `backend` and `backend/src` for the same repo: the exact
    key wins, then the first path segment, then an entry whose path is the
    value."""
    value = repo.replace("\\", "/").strip("/")
    if not value:
        return ""
    for candidate in (value, value.split("/")[0]):
        if candidate in entries:
            return candidate
    for key, entry in entries.items():
        if entry["path"].replace("\\", "/").strip("/") == value:
            return key
    return ""


def repos_named(declared: str) -> list[Path]:
    """The repos a non-empty `repo:` value names: a repos{} entry first (by key,
    then by path, see resolve_key), then a loose path match against the known
    repos, because a single-repo workspace names a repo that is the root itself
    and a value may point at a directory inside a repo. [] when it matches
    nothing - an unresolved name is UNKNOWN, never "every repo"."""
    parts = [p for p in declared.replace("\\", "/").split("/") if p]
    if ".." in parts:
        return []  # leaves the workspace; resolve_key would match `backend/..` as `backend`
    key = resolve_key(repo_entries(), declared)
    if key:
        return [repo_path(key)]
    # The loose path match below widens: `.` resolves to the workspace root, an
    # ancestor of every repo, so the ancestor test would name them all. Only a
    # repos{} entry may name it; here it fails closed.
    if all(p == "." for p in parts):
        return []
    target = (state.ROOT / declared).resolve()
    out = []
    for repo in repos():
        here = repo.resolve()
        if here == target or target in here.parents or here in target.parents:
            out.append(repo)
    return out


def repos_for_task(task: str) -> list[Path]:
    """The repos a task may legitimately live in.

    Task ids are allocated per WORKSPACE and consumed per REPO, so they collide.
    Measured in the `fin.local` tree: a frontend branch
    `enhancement/task-1236-calendar-redesign` existed while an unrelated backend
    task-1236 was waiting to close, and an unscoped scan read that stranger as
    "not merged yet" and parked the backend task forever. A task declaring
    `repo:` is only ever asked about that repo.

    Two cases, and they fail in opposite directions on purpose:

      - No `repo:` (absent or empty): every repo, the behaviour before the
        field existed.
      - A `repo:` that names no known repo: NOTHING. This is the one place
        where failing open is wrong. Merge detection decides when a task file
        moves to done/, so a misspelled or stale value that widened to every
        repo let a stranger's `[task-id]` commit close the task: data loss, not
        a blocked action. An empty list is UNKNOWN, and every caller already
        treats it as "cannot tell" and parks."""
    declared = state.task_repo(task)
    if not declared:
        return repos()
    return repos_named(declared)


def work_trees_for_task(task: str) -> list[Path]:
    """The directories a task's changes are read from. A recorded worktree that is
    usable is the only one; a run with no worktree of its own reads the repos it
    lives in, as before; a recorded one that is missing or malformed is UNKNOWN - an
    empty list, never the trunk checkout, so a caller that gets nothing parks."""
    import worktree  # lazy: worktree.py imports this module
    found = worktree.status(task)
    if found == worktree.STATUS_OK:
        tree = worktree.work_dir(task)
        return [tree] if tree is not None else []
    if found == worktree.STATUS_LEGACY:
        return repos_for_task(task)
    return []


def task_tagged_in_main(task: str) -> bool:
    """True only when EVERY repo that knows the task carries a commit on its
    trunk naming it (task_in_main), and at least one does.

    The one question the Stop hook's reconciler may act on. task_report() also
    reads a `branch:` the task file declares and any local branch whose name
    holds the id, and both are writable by the agent being judged: a forged
    carrier or a branch cut at an old merged tip reads as merged. A commit on
    main is not. Branches still count as AGAINST: a repo holding an unmerged
    branch for the task blocks the answer, which only ever fails closed.
    Unresolvable `repo:` -> no repos -> False."""
    known = []
    for repo in repos_for_task(task):
        if task_in_main(repo, task) is True:
            known.append(True)
        elif branch_for(repo, task):
            known.append(False)
    return bool(known) and all(known)


def task_report(task: str, do_fetch: bool = True, branch: str = "") -> list[dict]:
    """Where a task stands in every repo that knows about it.

    `branch` is the task's declared carrier branch (see declared_ref); when empty
    it is the one recorded at registration (state.task_branch), never the file's
    current `branch:`. Merge detection resolves against it when given, so two
    tasks sharing one branch are both detectable.

    An EMPTY list means no repo carries either signal - no branch and no tagged
    commit. That is UNKNOWN, never "nothing to merge": the caller must park, not
    finish the task.

    The loop runs over repos_for_task(), not repos(): a colliding task id in an
    unrelated repo is otherwise read as this task's unmerged branch."""
    out = []
    branch = branch or state.task_branch(task)
    for repo in repos_for_task(task):
        if do_fetch:
            fetch(repo)
        found = declared_ref(repo, branch) or branch_for(repo, task)
        # Merged is the UNION of the signals, and each one must be POSITIVE
        # evidence on its own: merge shape proves an ordinary merge and needs no
        # commit tag, the tag proves a squash merge that left no merge shape.
        # Either one is enough; only when both are silent (None) does the repo
        # stay unknown. Pointer containment is not a signal - see
        # merged_into_main.
        signals = [task_in_main(repo, task)]
        if found:
            signals.append(merged_into_main(repo, found))
        in_main = True if True in signals else (False if False in signals else None)
        if found is None and in_main is not True:
            continue
        out.append({
            "repo": repo.name,
            "branch": found,
            "pushed": is_pushed(repo, found),
            "in_main": in_main,
        })
    return out


def _main() -> int:
    parser = argparse.ArgumentParser(description="Branch ground truth")
    parser.add_argument("--repo", help="repo path relative to the workspace root")
    parser.add_argument("--task", help="task id, e.g. task-1234")
    parser.add_argument("--branch", help="branch or ref to test against main")
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    if args.task and not args.repo:
        print(json.dumps(task_report(args.task, do_fetch=not args.no_fetch), indent=2))
        return 0

    repo = state.ROOT / args.repo if args.repo else state.ROOT
    if not args.no_fetch:
        fetch(repo)

    if args.branch:
        print(json.dumps({"ref": args.branch,
                          "in_main": merged_into_main(repo, args.branch)}, indent=2))
    elif args.task:
        print(json.dumps({"task": args.task,
                          "branch": branch_for(repo, args.task),
                          "in_main": task_in_main(repo, args.task)}, indent=2))
    else:
        print(json.dumps({"repos": [r.name for r in repos()],
                          "main": main_branch()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

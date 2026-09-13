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
from pathlib import Path

import state

FETCH_TIMEOUT = 60
GIT_TIMEOUT = 20


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def repos() -> list[Path]:
    """Every git repo the pipeline works in. A workspace root that is not itself
    a repo holds them as subdirectories, so look one level down too."""
    pipeline = state.load_pipeline()
    configured = pipeline.get("repos")
    if configured:
        return [state.ROOT / str(r) for r in configured]

    found: list[Path] = []
    if (state.ROOT / ".git").exists():
        found.append(state.ROOT)
    for child in sorted(state.ROOT.iterdir()):
        try:
            if child.is_dir() and (child / ".git").exists():
                found.append(child)
            elif child.is_dir():
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir() and (grandchild / ".git").exists():
                        found.append(grandchild)
        except OSError:
            continue
    return found


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
    """True when the trunk already contains ref - ancestry, the one signal that
    needs nobody to have written a commit tag correctly. None when it cannot be
    told apart from a missing ref or a broken repo."""
    if not _ref_exists(repo, ref):
        return None
    return _is_ancestor(repo, ref, trunk(repo))


def task_in_main(repo: Path, task: str) -> bool | None:
    """True when the trunk carries a commit tagged with this task id.

    Reads the commit log rather than the branch tip, because a squash merge
    leaves the branch itself outside main while its content is in."""
    code, out = _git(repo, "log", trunk(repo), "--oneline",
                     f"--grep=\\[{task}\\]", "-1")
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


def task_report(task: str, do_fetch: bool = True, branch: str = "") -> list[dict]:
    """Where a task stands in every repo that knows about it.

    `branch` is the task's declared carrier branch (see declared_ref). Merge
    detection resolves against it when given, so two tasks sharing one branch
    are both detectable.

    An EMPTY list means no repo carries either signal - no branch and no tagged
    commit. That is UNKNOWN, never "nothing to merge": the caller must park, not
    finish the task."""
    out = []
    for repo in repos():
        if do_fetch:
            fetch(repo)
        found = declared_ref(repo, branch) or branch_for(repo, task)
        # Merged is the UNION of the signals: ancestry proves an ordinary merge
        # and needs no commit tag, the tag proves a squash merge that left no
        # ancestry. Either one is enough; only when both are silent (None) does
        # the repo stay unknown.
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

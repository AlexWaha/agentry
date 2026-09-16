#!/usr/bin/env python3
"""Record a human checkpoint decision for a task.

The orchestrator runs this only after the CEO explicitly approves in chat. It
flips the deterministic flag that pretool_gate.py checks before allowing
`git commit` / `git push`. `--reject` sends the task back to implement.

Trust note: nothing binds this to a real user message; it prevents accidental
skips and out-of-order commits, not a model that deliberately self-approves. The
hard stop is the Stop hook surfacing the diff and parking the task.

CLI:
    python approve.py --task task-0007 --gate commit
    python approve.py --task task-0007 --gate push
    python approve.py --task task-0007 --reject
    python approve.py --trunk-push
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pretool_gate
import state


def trunk_name() -> str:
    """The configured trunk, with the template placeholder resolved - read the
    same way pretool_gate.push_protected_branches() reads it."""
    name = str(state.load_pipeline().get("main_branch") or "main")
    return "main" if name.startswith("{{") else name


def record_trunk_push() -> int:
    """Record the CEO's approval to publish the trunk once.

    Solo mode only. In `pr` mode the trunk is reached by a pull request, so a
    marker there would be an approval for a step that has no legitimate
    spelling - it is refused at write time rather than written and ignored."""
    mode = pretool_gate.workflow_mode()
    if mode != pretool_gate.WORKFLOW_SOLO:
        print(json.dumps({"error": (
            f"workflow mode is '{mode}' - a push to the trunk is refused there in every "
            f"case, because the trunk is reached by a pull request. Only "
            f"'{pretool_gate.WORKFLOW_SOLO}' mode can publish the trunk by push.")}))
        return 2
    trunk = trunk_name()
    if trunk not in pretool_gate.PUSH_PROTECTED_ALWAYS:
        print(json.dumps({"error": (
            f"the configured trunk is '{trunk}', and the gate grants this approval for "
            f"{' / '.join(pretool_gate.PUSH_PROTECTED_ALWAYS)} only. Writing the marker "
            f"would record an approval no push can ever use.")}))
        return 2
    path = pretool_gate.trunk_push_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "trunk": trunk,
        "lane": state.LANE,
        "approved": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    print(json.dumps({"ok": True, "message": (
        f"trunk push approved - ONE push to '{trunk}' is now permitted in lane "
        f"'{state.LANE or 'default'}'; that push consumes the approval."), "marker": str(path)},
        indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a checkpoint decision")
    parser.add_argument("--task")
    parser.add_argument("--gate", choices=["commit", "push"])
    parser.add_argument("--reject", action="store_true",
                        help="send the task back to implement, clearing approvals")
    parser.add_argument("--trunk-push", action="store_true",
                        help="solo mode only: allow ONE push of the trunk to the remote")
    args = parser.parse_args()

    if args.trunk_push:
        return record_trunk_push()
    if not args.task:
        print(json.dumps({"error": "need --task task-XXXX (or --trunk-push)"}))
        return 2

    conn = state.connect()
    run = state.get_run(conn, args.task)
    if run is None:
        conn.close()
        print(json.dumps({"error": f"no active run for {args.task}"}))
        return 1

    if args.reject:
        first = state.stage_names(state.load_pipeline())[0]
        state.set_fields(conn, args.task, stage=first, stage_status=state.ST_IN_PROGRESS,
                         awaiting_human="", commit_approved=0, push_approved=0, retries=0,
                         continuations=0)
        msg = f"rejected - {args.task} sent back to '{first}'."
    elif args.gate == "commit":
        state.set_fields(conn, args.task, commit_approved=1)
        msg = f"commit approved for {args.task} - git commit now permitted."
    elif args.gate == "push":
        state.set_fields(conn, args.task, push_approved=1)
        msg = f"push approved for {args.task} - git push now permitted."
    else:
        conn.close()
        print(json.dumps({"error": "need --gate commit|push or --reject"}))
        return 2

    run = state.get_run(conn, args.task)
    conn.close()
    print(json.dumps({"ok": True, "message": msg, "run": run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

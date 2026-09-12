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
"""

from __future__ import annotations

import argparse
import json

import state


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a checkpoint decision")
    parser.add_argument("--task", required=True)
    parser.add_argument("--gate", choices=["commit", "push"])
    parser.add_argument("--reject", action="store_true",
                        help="send the task back to implement, clearing approvals")
    args = parser.parse_args()

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

# senior-backend-dev - persistent memory

Lessons this agent learned, in recall order. Auto-injected at startup (first ~200
lines / 25KB). Record per `.claude/rules/self-learning.md`; keep only high-value,
checkable lessons and curate when this file nears the injection window.

Format per lesson:

## [SIGNATURE / TRIGGER]
- **What:** one sentence - the mistake or surprise
- **Why:** one sentence - root cause
- **Fix:** imperative, specific, checkable rule
- **Date:** YYYY-MM-DD

## park-must-be-written-to-state
- **What:** `advance.py` returned `action: "park"` for a task waiting on a merge without writing `awaiting_human`, so `stop_gate.py` saw a free slot and offered the next backlog task on the same branch.
- **Why:** the JSON the model reads and the run row the hooks read are two different channels; only the row is state.
- **Fix:** in this harness, any code path that returns `action: "park"` must also `state.set_fields(..., awaiting_human=<reason>)`, and the path that unparks must clear it - grep both directions before trusting a park.
- **Date:** 2026-09-13

## absent-evidence-is-not-a-pass
- **What:** merge detection returning an empty report was read as "nothing to merge" and moved an unmerged task into `tasks/done/`.
- **Why:** a probe that fails open to "unknown" was consumed as a boolean, so a missing signal became a positive one.
- **Fix:** when a git/network probe can answer "cannot tell", branch on three states (true / false / unknown) and park on unknown; never let a falsy probe result advance a stage.
- **Date:** 2026-09-13

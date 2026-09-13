#!/usr/bin/env python3
"""Exit gate for the UI half of the test stage.

Unit tests prove logic in isolation. They say nothing about whether the page
renders, whether the route resolves, or whether the browser gets the shape the
server actually sends. A contract mismatch between a field the server returns
and the field the client reads passes every test on both sides and breaks the
feature on every single click. That happened, with two green suites.

So a change that touches the UI, or an endpoint the UI consumes, has to be
opened in a real browser, and the gate checks the evidence rather than the
claim. Claude in Chrome is preferred over a headless driver: the agent sees the
page a person would.

The evidence file lives at .claude/state/evidence/<task>.json:

    {
      "task": "task-1234",
      "tool": "claude-in-chrome",
      "role": "cfo",                  role the pages were exercised AS
      "routes": ["/reports/x", ...],  every changed route, opened
      "requests": [                   what the network panel showed
        {"url": "/api/v1/x", "status": 200}
      ],
      "notes": "free text"
    }

or, when the change genuinely cannot reach a screen:

    {"task": "task-1234", "not_applicable": "reason the UI is untouched"}

Refusals are deliberately specific, because a vague gate gets worked around:
  - superadmin as the tested role is rejected. It bypasses permission checks and
    hides exactly the failures those checks exist to catch.
  - any 4xx or 5xx among the recorded requests is rejected. An empty state looks
    identical to a failed fetch.

Exit 0 when satisfied, 1 with a reason otherwise.

CLI:
    python ui_evidence.py --task task-1234
"""

from __future__ import annotations

import argparse
import json
import sys

import state

EVIDENCE_DIR = state.STATE_DIR / "evidence"
BANNED_ROLES = {"superadmin", "super admin", "super-admin", "admin", "is_admin"}


def path_for(task: str):
    return EVIDENCE_DIR / f"{task}.json"


def safe(message: str) -> str:
    """Re-encode through stdout's own codec, replacing what it cannot carry.

    The gate message quotes the evidence file, so it carries whatever labels the
    UI uses. On a console whose stdout is cp1252 a non-ASCII label raised
    UnicodeEncodeError from print() and failed the stage - a red gate for a
    reason that has nothing to do with the evidence. Same class of defect as the
    cp1252 stdin decode the gates carried, and fixed the same way: name the
    codec explicitly instead of inheriting the host locale's."""
    enc = sys.stdout.encoding or "utf-8"
    return message.encode(enc, errors="replace").decode(enc, errors="replace")


def check(task: str) -> tuple[bool, str]:
    p = path_for(task)
    if not p.is_file():
        return False, (
            f"no UI evidence for {task}. Open every changed screen in a real browser "
            f"(Claude in Chrome preferred, Playwright acceptable), then write "
            f".claude/state/evidence/{task}.json with the routes you opened, the role "
            f"you were signed in as, and the status of every request the network panel "
            f"showed. If the change cannot reach a screen, record "
            f'{{"task": "{task}", "not_applicable": "<why>"}} instead - but say why.')

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"UI evidence for {task} is unreadable: {exc}"

    if data.get("not_applicable"):
        return True, f"UI check waived: {data['not_applicable']}"

    routes = [r for r in (data.get("routes") or []) if str(r).strip()]
    if not routes:
        return False, (f"UI evidence for {task} lists no routes. Name every screen the "
                       f"change touches and open each one.")

    role = str(data.get("role") or "").strip().lower()
    if not role:
        return False, (f"UI evidence for {task} does not say which role you were signed "
                       f"in as. The role decides what the page is allowed to show.")
    if role in BANNED_ROLES:
        return False, (f"UI evidence for {task} was collected as '{role}'. Superadmin "
                       f"bypasses permission checks and hides the failures they exist to "
                       f"catch. Sign in as the role that actually uses this feature.")

    requests = data.get("requests") or []
    if not requests:
        return False, (f"UI evidence for {task} records no requests. Watch the network "
                       f"panel: an empty state and a failed fetch look identical on screen.")

    bad = []
    for r in requests:
        try:
            code = int(r.get("status"))
        except (TypeError, ValueError):
            continue
        if code >= 400:
            bad.append(f"{r.get('url', '?')} -> {code}")
    if bad:
        return False, (f"UI evidence for {task} contains failed requests: "
                       f"{'; '.join(bad[:5])}. Fix them before the stage closes.")

    tool = data.get("tool") or "unspecified tool"
    return True, (f"UI verified with {tool} as {role}: {len(routes)} route(s), "
                  f"{len(requests)} request(s), none failing.")


def _main() -> int:
    parser = argparse.ArgumentParser(description="UI evidence exit gate")
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    ok, message = check(args.task)
    print(safe(message))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())

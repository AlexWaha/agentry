#!/usr/bin/env python3
"""PostToolUse(Bash) hook - lets the User read an advance.py refusal that waits on the User.

advance.py answers a refusal as JSON in a Bash tool result, which only the model
reads. When the refusal carries a `human` sentence (today: the spec is not
approved), this hook prints a fixed sentence for it as the hook's `systemMessage`
(the command must really run advance.py; see human_line), which Claude Code
shows to the person as "PostToolUse:Bash says: ...". Measured on claude.exe
2.1.295 with a real PostToolUse hook: the payload carries `tool_response.stdout`
as a string, and a top-level `systemMessage` from exit 0 reaches the terminal.

Task-0114, FR-50. Prints nothing and exits 0 on anything else, malformed input
included: this hook runs after the tool call and cannot deny anything, and the
refusal itself was decided by advance.py before it ever got here.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import pretool_gate

ADVANCE_RE = re.compile(r"(?:^|[\\/])advance\.py$", re.IGNORECASE)
TASK_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")

# The only sentence this hook ever shows. It is a fixed template filled with a
# task id that was checked against the command line, never text taken from the
# tool output: that output is whatever the command printed, so a `human` field
# read from it could be forged by any command whose text mentions advance.py.
# Kept equal to advance.SPEC_NOT_APPROVED_HUMAN (pinned by a test).
NOT_APPROVED_HUMAN = ("{task} cannot start: its spec is not approved. Read the spec "
                      "and approve it, or send the task back to planning.")


def advance_task(command: str) -> str:
    """The `--task` value of the advance.py invocation `command` runs, else "".

    Tokenised the way the gates tokenise, so `echo '{...}' # advance.py` (the
    name sits in a comment) or `cat advance.py` (argv0 is cat) do not count: the
    executed script has to BE advance.py. Beside it only `cd` may run, so
    nothing else can print the JSON this hook then trusts."""
    segments = pretool_gate.command_segments(command)
    if not segments:
        return ""
    runs, others = [], []
    for seg in segments:
        names = pretool_gate.exec_names_of(seg)
        if any(ADVANCE_RE.search(n) for n in names):
            runs.append(seg)
        elif any(pretool_gate.basename_no_ext(n) != "cd" for n in names):
            others.append(seg)
    if len(runs) != 1 or others:
        return ""
    seg = runs[0]
    for i, tok in enumerate(seg):
        if tok == "--task":
            value = seg[i + 1] if i + 1 < len(seg) else ""
        elif tok.startswith("--task="):
            value = tok.split("=", 1)[1]
        else:
            continue
        return value if TASK_ID_RE.fullmatch(value) else ""
    return ""


def human_line(payload: dict) -> str:
    """The notice for an advance.py `refused` result that waits on the User, else "".

    All of these must hold: the command runs advance.py for a task, the output
    starts with the JSON document (nothing printed in front of it), the document
    is a refusal for that same task, and advance.py marked it as waiting on the
    User by giving it a `human` sentence. The sentence shown is the fixed
    template, formatted with the task id, never the field."""
    if payload.get("tool_name") != "Bash":
        return ""
    command = str((payload.get("tool_input") or {}).get("command", ""))
    task = advance_task(command)
    if not task:
        return ""
    response = payload.get("tool_response")
    out = response.get("stdout") if isinstance(response, dict) else response
    if not isinstance(out, str) or not out.startswith("{"):
        return ""
    doc, _ = json.JSONDecoder().raw_decode(out)
    if not isinstance(doc, dict) or doc.get("action") != "refused" or doc.get("task") != task:
        return ""
    human = doc.get("human")
    if not isinstance(human, str) or not human.strip():
        return ""
    return NOT_APPROVED_HUMAN.format(task=task)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        line = human_line(json.loads(raw))
        if line:
            sys.stdout.write(json.dumps({"systemMessage": line}) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

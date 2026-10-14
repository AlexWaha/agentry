"""Shared assertion for tests that pin "this read stays allowed" across agent profiles.

task-0116 made the readonly and docs profiles default-deny on Bash: a program that is
not on agent_gate.ALLOWED_PROGRAMS is refused whatever it does. Several older tests
pin that a script or redirect CHECK does not misfire on a harmless command, and run
that command through every profile with `make`, `python -c`, a function definition or
a wrapper the allowlist does not know. For those, the allowlist's own deny is not the
failure being tested for: any OTHER deny still is.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import agent_gate


def assert_not_denied(case, profile: str, code: int, err: str) -> None:
    """Pass on exit 0; for readonly/docs also pass when the only reason is the
    allowlist (agent_gate.ALLOWLIST_PHRASE). dev has no allowlist, so it must be 0."""
    if code == 2 and profile != "handle_dev" and agent_gate.ALLOWLIST_PHRASE in err:
        return
    case.assertEqual(0, code, err)

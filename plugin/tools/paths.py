"""The two roots of the harness, defined once.

A plugin install splits the tree in two (spec-0001 FR-65):

  PLUGIN_ROOT   where this code lives: scripts, skills, hook registration. It is
                derived from this file's own location, so a worktree's
                `plugin/tools` runs its own code and a cached plugin copy runs
                itself.
  PROJECT_ROOT  the user's project: `.agentry/` (state, tasks, plans, memory),
                `.claude/` (rules, agents, settings). It is NOT derived from the
                code's location - the plugin may sit anywhere - and NOT from the
                current directory.

PROJECT_ROOT comes from the CLAUDE_PROJECT_DIR environment variable and from
nothing else. There is deliberately no walk up from the cwd looking for
`.agentry/pipeline.json`: this repository carries a tracked `.agentry/` template
in `src/` and in every `src-wt/<task>/` worktree, so a walk would find the
template and write run state into the wrong tree (task-0038 plan, section C4).
An unset variable is a hard stop with exit 2, never a guess.

Where the variable is visible (measured, docs/technical/plugin-probes-2.1.299.md,
P2): in every hook, and in a Bash tool only when the plugin's SessionStart hook
exported it into $CLAUDE_ENV_FILE (hooks/session_start.py does). A script that
starts a child session or a daemon sets it in the child's environment explicitly
instead of inheriting it, because an inherited value can be stale.

Tests set CLAUDE_PROJECT_DIR in tests/__init__.py, before any tool module is
imported, to the checkout that holds the tests. They never rely on the cwd.

Import-time exit 2 is safe in hooks because the platform sets the variable for
every hook event; a script that is run by hand outside a session gets a one-line
message saying what to export.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ENV = "CLAUDE_PROJECT_DIR"
PLUGIN_ENV = "AGENTRY_ROOT"

# tools/paths.py -> tools -> the plugin root (the folder holding tools/, skills/,
# hooks/ and .claude-plugin/).
PLUGIN_ROOT = Path(__file__).resolve().parents[1]

UNSET_MESSAGE = (
    f"{PROJECT_ENV} is not set, so the project root is unknown. The harness scripts "
    f"never guess it from the current directory. Run inside a Claude Code session "
    f"(its SessionStart hook exports it), or export {PROJECT_ENV}=<project root> first."
)


def project_root() -> Path:
    """The project root named by CLAUDE_PROJECT_DIR; exit 2 with a message when unset."""
    raw = os.environ.get(PROJECT_ENV, "").strip()
    if not raw:
        sys.stderr.write(UNSET_MESSAGE + "\n")
        raise SystemExit(2)
    return Path(raw).resolve()


def __getattr__(name: str):
    """PEP 562: PROJECT_ROOT is read from the environment on first use and kept.

    Lazy on purpose. A script that needs only PLUGIN_ROOT - the setup tools, which
    take their target on the command line - imports this module without needing
    CLAUDE_PROJECT_DIR; every script that touches project data reads PROJECT_ROOT
    at import (state.ROOT) and so fails at once, not halfway through its work."""
    if name == "PROJECT_ROOT":
        root = project_root()
        globals()[name] = root
        return root
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

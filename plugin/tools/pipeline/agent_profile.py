"""Which gate profile binds a dispatched subagent, read from its own definition.

The agent_gate and dangerous_patterns hooks are registered once, plugin-wide
(hooks/hooks.json), because Claude Code ignores `hooks:` on plugin agents and
`${CLAUDE_PLUGIN_ROOT}` exists only inside plugin hooks. A plugin-level hook fires
for the main thread, for every roster agent and for built-in or foreign agents
alike, so the hook has to work out who is calling. It does that here:

  1. No agent identity in the payload: the main thread. The orchestrator is bound
     by pretool_gate, not by a profile.
  2. An identity (`agent_type`, measured on 2.1.299: `senior-backend-dev` for a
     project agent, `general-purpose` / `Explore` for built-ins, `plugin:name` for
     a plugin agent): read `<project>/.claude/agents/<agent_type>.md` and take the
     `profile:` key (dev, docs or readonly) and the `allow_scripts:` list from its
     frontmatter.
  3. Anything that does not resolve - no definition, an unreadable or malformed
     one, an invalid profile, a name that is not a plain file stem - is READONLY.
     A plugin may only tighten what a project allows, so an unknown caller fails
     closed (decision D3 of task-0038): built-ins and foreign agents lose write
     access.

The definition is read from the PROJECT root, never from the plugin and never from
the payload cwd. Subagent profiles cannot write `.claude/agents/` (it is part of the
live harness every profile is refused), so the file is trusted.

Standard library only, and never raises: both callers are hooks.
"""

from __future__ import annotations

import re
from pathlib import Path

PROFILES = ("dev", "docs", "readonly")
FALLBACK_PROFILE = "readonly"

# Payload keys that name the caller, in the order pretool_gate.evaluate() reads
# them. Any non-empty value of any of them (or a subagent transcript path) marks a
# subagent; the first non-empty one names it.
IDENTITY_KEYS = ("agent_type", "subagent_type", "agent_name", "agent_id")
NAME_KEYS = IDENTITY_KEYS[:3]

# A plain file stem: no separator, no colon (a plugin agent arrives as `plugin:name`,
# and a colon in a Windows path opens an alternate data stream), no leading dot.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SCRIPT_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
PROFILE_KEY_RE = re.compile(r"^profile:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
SCRIPTS_KEY_RE = re.compile(r"^allow_scripts:[ \t]*\[([^\]\r\n]*)\][ \t]*$", re.MULTILINE)


def is_subagent(payload: dict) -> bool:
    """True when the hook payload comes from a dispatched subagent. Same signals as
    pretool_gate.evaluate(): an identity key, or a transcript under `subagents/`."""
    if any(str(payload.get(k) or "").strip() for k in IDENTITY_KEYS):
        return True
    path = str(payload.get("transcript_path") or "").replace("\\", "/").lower()
    return "/subagents/" in path or path.rsplit("/", 1)[-1].startswith("agent-")


def agent_name(payload: dict) -> str:
    """The name the payload gives the caller, '' when it gives none."""
    for key in NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def definition(name: str, root: Path | str | None) -> tuple[str, tuple[str, ...]] | None:
    """(profile, allow_scripts) from `<root>/.claude/agents/<name>.md`, or None when
    the name is not a plain stem, the file is missing or unreadable, has no
    frontmatter, or names a profile that is not one of PROFILES."""
    if not root or not NAME_RE.fullmatch(name):
        return None
    try:
        text = (Path(root) / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return None
    block = FRONTMATTER_RE.match(text)
    if not block:
        return None
    found = PROFILE_KEY_RE.search(block.group(1))
    if not found or found.group(1) not in PROFILES:
        return None
    scripts = SCRIPTS_KEY_RE.search(block.group(1))
    items = [s.strip().strip("'\"") for s in scripts.group(1).split(",")] if scripts else []
    return found.group(1), tuple(s for s in items if SCRIPT_RE.fullmatch(s))


def resolve(payload: dict, root: Path | str | None) -> tuple[str, tuple[str, ...]]:
    """(profile, allow_scripts) for a SUBAGENT payload; (FALLBACK_PROFILE, ()) when it
    does not resolve. Callers ask is_subagent() first - the main thread has no profile."""
    try:
        return definition(agent_name(payload), root) or (FALLBACK_PROFILE, ())
    except Exception:  # a hook helper must not raise; an unknown caller is readonly
        return FALLBACK_PROFILE, ()

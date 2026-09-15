#!/usr/bin/env python3
"""FR-35 - count the dispatch payload, in tokens, for every injection path.

Phase 1's target is a number (NFR-1: a dev agent starts a dispatch under 40,000
tokens, down from roughly 145,000) and "much smaller now" is not the number.
This script produces it and is committed so the number can be re-produced rather
than quoted.

The payload is what a dispatched agent's context already holds before it reads
anything of its own. Per spec-0001 that is: the root CLAUDE.md, .claude/CLAUDE.md
with its @import set, the agent definition file, and all SubagentStart hook
output. Skills are measured too - an agent's `skills:` files are preloaded the
same way and the spec's list simply predates noticing them - but they are broken
out on their own line so a spec-conformant subtotal is still readable.

Two kinds of number, never blurred (the `how` column says which):
  measured  the hook was RUN, with a SubagentStart payload naming the agent, and
            its real stdout was parsed; the count is of the additionalContext
            string, because the JSON envelope around it is not what reaches the
            model.
  computed  the file's text length on disk. Claude Code does the loading, so
            this asserts the file is delivered whole and delivers nothing else.
            It is an upper-ish bound, not an observation.

The dispatch prompt itself is NOT counted. It is written per dispatch and exists
nowhere on disk, so there is no honest file to measure; it is reported as unknown
rather than estimated.

CLI:
    python payload.py                 worst agent per profile, then the roster
    python payload.py --agent NAME    one agent's full breakdown
    python payload.py --all           every agent's full breakdown
    python payload.py --json          machine-readable, same numbers
    python payload.py --root DIR      measure another checkout (the `before`)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# The conversion, fixed by FR-35 and declared here rather than folded into an
# expression so the arithmetic is auditable. Four characters per token is a
# rule of thumb for English prose; it is not a tokenizer. Every number this
# script prints inherits that approximation, and the DoD forbids moving this
# value to reach the target.
TOKEN_CHARS = 4

HERE = Path(__file__).resolve()
DEFAULT_ROOT = HERE.parents[2]

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
IMPORT_RE = re.compile(r"^@(rules/[A-Za-z0-9._-]+\.md)\s*$", re.MULTILINE)
PROFILE_RE = re.compile(r"agent_gate\.py\"?\s+--profile\s+([a-z]+)")
LIST_KEY_RE = re.compile(r"^(skills|rules):[ \t]*(.*)$", re.MULTILINE)
BLOCK_ITEM_RE = re.compile(r"^[ \t]+-[ \t]*(.+?)[ \t]*$")
ENV_RE = re.compile(r"\$\{?CLAUDE_PROJECT_DIR\}?")

MEASURED = "measured"
COMPUTED = "computed"


def tokens(text: str) -> int:
    return len(text) // TOKEN_CHARS


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def frontmatter(text: str) -> str:
    block = FRONTMATTER_RE.match(text)
    return block.group(1) if block else ""


def list_key(front: str, name: str) -> list[str]:
    """A YAML list key from agent frontmatter, in declared order.

    Same three value forms inject_rules.declared_rules accepts (flow, scalar,
    block), because the agent files use the block form for `skills:` and the
    flow form is legal beside it.
    """
    for key, value in LIST_KEY_RE.findall(front):
        if key != name:
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
        elif value:
            items = value.split(",")
        else:
            items = []
            start = front.index(f"{key}:") + len(key) + 1
            for line in front[start:].splitlines()[1:]:
                if not line.strip():
                    continue
                item = BLOCK_ITEM_RE.match(line)
                if not item:
                    break
                items.append(item.group(1))
        return [c for c in (i.strip().strip("'\"") for i in items) if c]
    return []


class Row:
    def __init__(self, label: str, chars: int, how: str, note: str = ""):
        self.label = label
        self.chars = chars
        self.how = how
        self.note = note

    @property
    def tokens(self) -> int:
        return self.chars // TOKEN_CHARS

    def as_dict(self) -> dict:
        return {"path": self.label, "chars": self.chars, "tokens": self.tokens,
                "how": self.how, "note": self.note}


class Harness:
    """One checkout, so `--root` can point at the pre-Phase-1 tree."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.agents_dir = self.root / ".claude" / "agents"
        self.rules_dir = self.root / ".claude" / "rules"
        self.skills_dir = self.root / ".claude" / "skills"

    def settings(self) -> dict:
        try:
            return json.loads(read(self.root / ".claude" / "settings.json") or "{}")
        except ValueError:
            return {}

    def agent_names(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.agents_dir.glob("*.md"))
        except OSError:
            return []

    def profile(self, name: str) -> str:
        """The gate profile an agent declares, read from its own agent_gate.py
        hook command. That string is what actually governs the agent, so it
        cannot drift from a table kept somewhere else."""
        found = PROFILE_RE.search(read(self.agents_dir / f"{name}.md"))
        return found.group(1) if found else "unknown"

    def imported_rules(self) -> set[str]:
        """Filenames named by an `@rules/...` line in .claude/CLAUDE.md."""
        text = read(self.root / ".claude" / "CLAUDE.md")
        return {Path(entry).name for entry in IMPORT_RE.findall(text)}

    def core_rules(self) -> list[Path]:
        """The rule files EVERY context gets: the .claude/rules/ walk, minus
        claudeMdExcludes.

        Modelled on the walk rather than on the @import list because the walk is
        what actually loads them - .claude/CLAUDE.md says so itself, and the
        @rules/ lines exist as a live include naming the same files, in case a
        build ships without the walk. The distinction is not academic: measured
        on this repository, the pre-Phase-1 tree at 623d4e1 has 20 rule files, an
        empty claudeMdExcludes and ZERO @rules/ lines, so reading the @import
        list would have scored its always-loaded rules at nothing and reported a
        baseline far below the truth.
        """
        try:
            files = sorted(self.rules_dir.glob("*.md"))
        except OSError:
            return []
        excluded = {Path(str(e)).name for e in self.settings().get("claudeMdExcludes", [])}
        return [p for p in files if p.name not in excluded]

    def check_rule_channels(self) -> str:
        """'' when the two rule channels agree, else a one-line warning.

        Where an @import set exists it must name exactly the non-excluded files.
        A file the walk loads but no @rules/ line names still reaches every
        agent; a file named but excluded is a contradiction between the two
        edits that are supposed to move together. Either way the count above is
        only as good as this agreement, so a disagreement is printed, not
        swallowed.
        """
        imported = self.imported_rules()
        if not imported:
            return ""
        walked = {p.name for p in self.core_rules()}
        if imported != walked:
            missing = ", ".join(sorted(walked - imported)) or "none"
            extra = ", ".join(sorted(imported - walked)) or "none"
            return ("WARNING: the rules walk and the @import set disagree - "
                    f"walked but not imported: {missing}; imported but excluded: {extra}")
        return ""

    def hook_entries(self, agent: str) -> list[tuple[str, str]]:
        """[(command, label)] for every SubagentStart hook that fires for this
        agent, in settings order.

        An entry with no matcher fires for every agent - that is how the rule
        injection reaches agents nobody remembered to list. A matcher is a
        regex over the agent type; fullmatch is used because every matcher in
        this harness is a plain alternation of exact agent names, and search
        would let a future `reviewer-lite` inherit `reviewer`'s hooks silently.
        """
        out = []
        for entry in self.settings().get("hooks", {}).get("SubagentStart", []) or []:
            matcher = entry.get("matcher")
            if matcher:
                try:
                    if not re.fullmatch(matcher, agent):
                        continue
                except re.error:
                    continue
            for hook in entry.get("hooks", []) or []:
                cmd = hook.get("command", "")
                if hook.get("type") == "command" and cmd:
                    out.append((cmd, hook_label(cmd)))
        return out

    def run_hook(self, cmd: str, agent: str) -> tuple[str, str]:
        """(additionalContext, note) from really running one hook.

        $CLAUDE_PROJECT_DIR is expanded here instead of by a shell: the command
        is a POSIX-quoted string, cmd.exe would not expand it at all, and a
        shell=True call would resolve it against this session's root rather
        than --root. The env var is set as well, for a hook that reads it
        directly.
        """
        expanded = ENV_RE.sub(self.root.as_posix(), cmd)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        try:
            proc = subprocess.run(shlex.split(expanded), input=json.dumps({
                "hook_event_name": "SubagentStart",
                "agent_type": agent,
                "agent_id": "payload-measurement",
            }), capture_output=True, text=True, timeout=60, env=env, cwd=str(self.root))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return "", f"hook did not run ({type(exc).__name__})"
        if not proc.stdout.strip():
            return "", "no output" if proc.returncode == 0 else f"exit {proc.returncode}, no output"
        try:
            block = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            # Plain stdout is DISCARDED on SubagentStart (task-0081), so text
            # outside the envelope reaches no agent and is worth zero here. Say
            # so rather than counting it.
            return "", f"stdout is not a SubagentStart envelope, delivers nothing ({len(proc.stdout)} chars)"
        return block, ""

    def rows(self, agent: str) -> list[Row]:
        agent_file = self.agents_dir / f"{agent}.md"
        text = read(agent_file)
        front = frontmatter(text)

        rows = [
            Row("root CLAUDE.md", len(read(self.root / "CLAUDE.md")), COMPUTED),
            Row(".claude/CLAUDE.md", len(read(self.root / ".claude" / "CLAUDE.md")), COMPUTED),
        ]

        core = self.core_rules()
        rows.append(Row(f"core rules (always loaded, {len(core)} files)",
                        sum(len(read(p)) for p in core), COMPUTED))
        rows.append(Row(f"agent file ({agent}.md)", len(text), COMPUTED))

        skills = list_key(front, "skills")
        missing = [s for s in skills if not (self.skills_dir / s / "SKILL.md").is_file()]
        rows.append(Row(f"preloaded skills ({len(skills)})",
                        sum(len(read(self.skills_dir / s / "SKILL.md")) for s in skills),
                        COMPUTED,
                        f"not found: {', '.join(missing)}" if missing else ""))

        hooks = self.hook_entries(agent)
        for cmd, label in hooks:
            block, note = self.run_hook(cmd, agent)
            rows.append(Row(f"SubagentStart hook: {label}", len(block), MEASURED, note))
        if not hooks:
            rows.append(Row("SubagentStart hooks (none fire for this agent)", 0, MEASURED))
        return rows


def hook_label(cmd: str) -> str:
    """A readable name for a hook: its script plus the flags that change what
    it emits. Two memory hooks differ only in --kinds and --limit, and a
    breakdown that printed both as 'inject.py' would hide which one fired."""
    parts = shlex.split(ENV_RE.sub("", cmd)) or [cmd]
    name = Path(parts[0]).name
    if name.lower().startswith("python"):
        parts = parts[1:] or parts
        name = Path(parts[0]).name
    return " ".join([name, *parts[1:]]).strip()


def total_chars(rows: list[Row]) -> int:
    return sum(r.chars for r in rows)


def print_breakdown(harness: Harness, agent: str, rows: list[Row], out) -> None:
    print(f"\nagent: {agent}  (profile: {harness.profile(agent)})", file=out)
    print(f"  {'injection path':<46}{'chars':>9}{'tokens':>9}  how", file=out)
    for row in rows:
        note = f"   [{row.note}]" if row.note else ""
        print(f"  {row.label:<46}{row.chars:>9}{row.tokens:>9}  {row.how}{note}", file=out)
    chars = total_chars(rows)
    print(f"  {'TOTAL':<46}{chars:>9}{chars // TOKEN_CHARS:>9}", file=out)
    print("  dispatch prompt                               "
          "  unknown   unknown  not on disk, written per dispatch", file=out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure the dispatch payload per agent")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="harness checkout to measure (default: this one)")
    parser.add_argument("--agent", default="", help="one agent's breakdown")
    parser.add_argument("--all", action="store_true", help="every agent's breakdown")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    harness = Harness(Path(args.root))
    names = harness.agent_names()
    if not names:
        print(f"no agent files under {harness.agents_dir}", file=sys.stderr)
        return 1
    if args.agent:
        if args.agent not in names:
            print(f"unknown agent {args.agent!r}", file=sys.stderr)
            return 1
        names = [args.agent]

    measured = {name: harness.rows(name) for name in names}
    warning = harness.check_rule_channels()

    if args.json:
        print(json.dumps({
            "root": str(harness.root),
            "token_chars": TOKEN_CHARS,
            "warning": warning,
            "agents": {
                name: {
                    "profile": harness.profile(name),
                    "paths": [r.as_dict() for r in rows],
                    "total_chars": total_chars(rows),
                    "total_tokens": total_chars(rows) // TOKEN_CHARS,
                } for name, rows in measured.items()},
        }, indent=2))
        return 0

    print(f"dispatch payload, root {harness.root}, TOKEN_CHARS = {TOKEN_CHARS}")
    if warning:
        print(warning)

    if args.agent or args.all:
        for name in names:
            print_breakdown(harness, name, measured[name], sys.stdout)
    else:
        # The budget question is about the worst case, not the average: an
        # agent is dispatched as itself, never as the mean of its profile.
        by_profile: dict[str, list[str]] = {}
        for name in names:
            by_profile.setdefault(harness.profile(name), []).append(name)
        for profile in sorted(by_profile):
            worst = max(by_profile[profile], key=lambda n: total_chars(measured[n]))
            print(f"\n=== profile {profile}: {len(by_profile[profile])} agent(s), "
                  f"largest is {worst} ===")
            print_breakdown(harness, worst, measured[worst], sys.stdout)

    print(f"\n{'agent':<30}{'profile':<10}{'chars':>9}{'tokens':>9}")
    for name in sorted(names, key=lambda n: -total_chars(measured[n])):
        chars = total_chars(measured[name])
        print(f"{name:<30}{harness.profile(name):<10}{chars:>9}{chars // TOKEN_CHARS:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

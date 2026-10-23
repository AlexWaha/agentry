"""task-0038 (FR-65): the plugin root and the project root never cross.

The harness is split in two. The plugin (`plugin/`, `${CLAUDE_PLUGIN_ROOT}`) holds
scripts, skills and the hook registration; the project (`.claude/` and `.agentry/`,
`${CLAUDE_PROJECT_DIR}`) holds rules, agents, settings and every work product. The
acceptance criteria read "grep confirms no crossed reference in either direction"
and "every hook registered in the plugin" resolves, so both are pinned here as
tests instead of one-off greps that nobody re-runs:

  direction A  plugin code, skills and hooks.json never name a project path by
               going through the plugin root (`PLUGIN_ROOT / ".agentry"`,
               `${CLAUDE_PLUGIN_ROOT}/../`, `parents[N]` past the plugin root).
  direction B  the project side never hardcodes a plugin path (the old
               `.claude/tools|hooks|skills/` locations, `plugin/tools`,
               `${CLAUDE_PLUGIN_ROOT}` in settings or agent files).
  AC5          every command in hooks.json points at a script that exists.

Each grep is also run against a planted offender, so an empty result means the
pattern works and not that it matches nothing.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
PLUGIN = TOOLS_DIR.parent
REPO = PLUGIN.parent
CLAUDE = REPO / ".claude"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"
QUOTED_SCRIPT = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"')

# Direction A. A path that starts at the plugin root and lands in project data.
PLUGIN_TO_PROJECT_TEXT = re.compile(
    r"(?:\$\{?(?:CLAUDE_PLUGIN_ROOT|AGENTRY_ROOT)\}?)/"
    r"(?:\.\.|\.agentry|\.claude|rules/|agents/|settings|CLAUDE\.md)")
# The same in Python: the plugin root constant and a project name on one line, or a
# step above the plugin root.
PLUGIN_TO_PROJECT_CODE = re.compile(
    r"(?:PLUGIN_ROOT|plugin_root)[^\n]*(?:\.agentry|\.claude(?!-plugin))"
    r"|(?:\.agentry|\.claude(?!-plugin))[^\n]*(?:PLUGIN_ROOT|plugin_root)"
    r"|PLUGIN_ROOT\.parent|PLUGIN_ROOT\s*/\s*[\"']\.\.[\"']")
PARENTS_INDEX = re.compile(r"\.parents\[(\d+)\]")

# Direction B. A project file naming where the plugin lives.
OLD_PLUGIN_LOCATIONS = re.compile(r"\.claude/(?:tools|hooks)\b|\.claude/skills/(?!verify\b)")
NEW_PLUGIN_LOCATIONS = re.compile(r"plugin/(?:tools|skills|hooks)\b|CLAUDE_PLUGIN_ROOT")

# tools/setup/ turns a SOURCE checkout of the harness into a project (fleet rollout,
# onboarding). It is given that checkout and reads its `.claude/` on purpose; whether
# it stays in the plugin is decided later (plan C10, task-0038), and it is moved
# verbatim here. Everything else is runtime code and is held to the rule.
RUNTIME_EXEMPT = ("tools/setup/", "tools/tests/")

# `rules/orchestration.md` carries the release runbook and the history of the move to
# the plugin, so it names the old `.claude/tools` location on purpose and is exempt
# from the check that project prose no longer points there.
PROJECT_PROSE_EXEMPT = {"orchestration.md"}


def rel(path: Path) -> str:
    return path.relative_to(PLUGIN).as_posix()


def runtime_python() -> list[Path]:
    return [p for p in sorted(PLUGIN.rglob("*.py"))
            if "__pycache__" not in p.parts and not rel(p).startswith(RUNTIME_EXEMPT)]


def registered() -> list[tuple[str, str | None, dict]]:
    """[(event, matcher, hook)] for every hook in hooks.json."""
    hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    return [(event, group.get("matcher"), hook)
            for event, groups in hooks.items() for group in groups for hook in group["hooks"]]


class GrepSanityTest(unittest.TestCase):
    """A grep that cannot fail proves nothing; plant one offender per pattern."""

    def test_direction_a_patterns_catch_a_planted_crossing(self):
        for text in ("${CLAUDE_PLUGIN_ROOT}/.agentry/state", "$AGENTRY_ROOT/../.claude",
                     "${AGENTRY_ROOT}/rules/security.md"):
            with self.subTest(text=text):
                self.assertTrue(PLUGIN_TO_PROJECT_TEXT.search(text))
        for line in ('x = PLUGIN_ROOT / ".agentry"', 'y = paths.PLUGIN_ROOT.parent',
                     'z = plugin_root / ".claude" / "rules"'):
            with self.subTest(line=line):
                self.assertTrue(PLUGIN_TO_PROJECT_CODE.search(line))

    def test_direction_a_patterns_pass_the_legitimate_forms(self):
        for text in ("${CLAUDE_PLUGIN_ROOT}/tools/hooks/x.py", "$AGENTRY_ROOT/skills/README.md"):
            with self.subTest(text=text):
                self.assertIsNone(PLUGIN_TO_PROJECT_TEXT.search(text))
        self.assertIsNone(PLUGIN_TO_PROJECT_CODE.search('d = state.ROOT / ".agentry" / "state"'))
        # The plugin's own manifest folder lives inside the plugin root; it is not a project path.
        self.assertIsNone(PLUGIN_TO_PROJECT_CODE.search('m = plugin_root / ".claude-plugin" / "plugin.json"'))

    def test_direction_b_patterns_catch_a_planted_plugin_path(self):
        for text in (".claude/tools/pipeline/state.py", ".claude/hooks/x.sh",
                     ".claude/skills/bug-fix", "plugin/tools/x.py", "${CLAUDE_PLUGIN_ROOT}/x"):
            with self.subTest(text=text):
                self.assertTrue(OLD_PLUGIN_LOCATIONS.search(text)
                                or NEW_PLUGIN_LOCATIONS.search(text))

    def test_the_verify_skill_stays_project_side(self):
        # The one skill that is not in the plugin (plan D4).
        self.assertIsNone(OLD_PLUGIN_LOCATIONS.search(".claude/skills/verify/SKILL.md"))
        self.assertTrue((CLAUDE / "skills" / "verify" / "SKILL.md").is_file())


class PluginToProjectTest(unittest.TestCase):
    """Direction A: the plugin never reaches into a project's work product."""

    def test_python_code_builds_no_project_path_from_the_plugin_root(self):
        offenders = []
        for path in runtime_python():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PLUGIN_TO_PROJECT_CODE.search(line):
                    offenders.append(f"{rel(path)}:{number}: {line.strip()}")
        self.assertEqual([], offenders)

    def test_no_script_climbs_past_the_plugin_root_from_its_own_location(self):
        # tools/<dir>/x.py sits two folders under the plugin root, so parents[2] is
        # the plugin root and parents[3] is the repository around it.
        offenders = []
        for path in runtime_python():
            deepest = len(path.relative_to(PLUGIN).parts) - 1
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for index in PARENTS_INDEX.findall(line):
                    if int(index) > deepest:
                        offenders.append(f"{rel(path)}:{number}: parents[{index}] > {deepest}")
        self.assertEqual([], offenders)

    def test_skills_and_hooks_json_name_no_project_path_through_the_plugin_root(self):
        offenders = []
        texts = [*sorted((PLUGIN / "skills").rglob("*.md")), HOOKS_JSON]
        for path in texts:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PLUGIN_TO_PROJECT_TEXT.search(line):
                    offenders.append(f"{rel(path)}:{number}: {line.strip()}")
        self.assertEqual([], offenders)

    def test_no_hook_command_resolves_through_the_project_root(self):
        # hooks.json commands run from the plugin; `$CLAUDE_PROJECT_DIR` belongs to
        # project settings (and to the scripts, which read it themselves).
        for event, _matcher, hook in registered():
            with self.subTest(event=event, command=hook["command"]):
                self.assertNotIn("CLAUDE_PROJECT_DIR", hook["command"])
                self.assertNotIn(".claude", hook["command"])
                self.assertNotIn(".agentry", hook["command"])

    def test_the_plugin_ships_no_project_data(self):
        # Leak guard, task-0041: nothing under .agentry/ may travel with the plugin.
        crossed = [rel(p) for p in PLUGIN.rglob("*")
                   if ".agentry" in p.relative_to(PLUGIN).parts]
        self.assertEqual([], crossed)


class ProjectToPluginTest(unittest.TestCase):
    """Direction B: the project never hardcodes a plugin path."""

    def test_settings_json_names_no_plugin_path(self):
        # A plugin cannot ship `statusLine` (probe P5, plugin-probes-2.1.299.md) and the
        # template registers none (task-0137), so no key may name a plugin location.
        settings = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("hooks", settings, "hook registration lives in plugin/hooks/hooks.json")
        self.assertNotIn("statusLine", settings)
        text = json.dumps(settings)
        self.assertIsNone(OLD_PLUGIN_LOCATIONS.search(text))
        self.assertIsNone(NEW_PLUGIN_LOCATIONS.search(text))

    def test_agent_definitions_name_no_plugin_path(self):
        offenders = []
        for path in sorted((CLAUDE / "agents").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if OLD_PLUGIN_LOCATIONS.search(text) or NEW_PLUGIN_LOCATIONS.search(text):
                offenders.append(path.name)
            self.assertNotRegex(text, r"(?m)^hooks:", f"{path.name} still carries a hooks: block")
        self.assertEqual([], offenders)

    def test_rules_and_orchestrator_docs_name_no_old_plugin_location(self):
        # `${CLAUDE_PLUGIN_ROOT}` and `plugin/` are allowed here: .claude/CLAUDE.md
        # explains the layout and names them; what must be gone is the old
        # `.claude/tools` family these files used to point at.
        files = [p for p in sorted((CLAUDE / "rules").glob("*.md"))
                 if p.name not in PROJECT_PROSE_EXEMPT]
        files += [CLAUDE / "CLAUDE.md", REPO / "CLAUDE.md"]
        offenders = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD_PLUGIN_LOCATIONS.search(line):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}")
        self.assertEqual([], offenders)


class HooksJsonTest(unittest.TestCase):
    """AC5, the part that can be checked without running anything: every command
    names a script that exists. The run of every hook is the S7 evidence."""

    def test_the_manifest_names_the_plugin(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("agentry", manifest["name"])
        # Releases are semver plus a git tag; `plugin update` follows the version.
        self.assertRegex(manifest.get("version", ""), r"^\d+\.\d+\.\d+$")

    def test_the_marketplace_serves_the_plugin_folder(self):
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("agentry", market["name"])
        self.assertEqual([("agentry", "./plugin")], [(p["name"], p["source"]) for p in market["plugins"]])

    def test_every_command_script_exists_inside_the_plugin(self):
        hooks = registered()
        self.assertGreater(len(hooks), 40)
        for event, _matcher, hook in hooks:
            command = hook["command"]
            if PLACEHOLDER not in command:
                # The one inline command, `rm -f nul NUL`, has no path at all.
                self.assertEqual("rm -f nul NUL", command)
                continue
            with self.subTest(event=event, command=command):
                found = QUOTED_SCRIPT.search(command)
                self.assertIsNotNone(found, "the placeholder must sit inside double quotes "
                                            "(a plugin root with a space breaks it otherwise)")
                script = PLUGIN / found.group(1)
                self.assertTrue(script.is_file(), f"{found.group(1)} does not exist")
                self.assertTrue(command.startswith("python "), command)
                self.assertEqual(1, command.count(PLACEHOLDER))

    def test_every_entry_has_a_timeout(self):
        for event, _matcher, hook in registered():
            with self.subTest(event=event, command=hook["command"]):
                self.assertIsInstance(hook.get("timeout"), int)

    def test_every_gate_is_registered_on_its_event(self):
        # Hook parity with the registrations that used to sit in settings.json and in
        # the agent files: losing one silently disables a gate.
        by_event: dict[str, list[str]] = {}
        for event, _matcher, hook in registered():
            by_event.setdefault(event, []).append(hook["command"])
        expected = {
            "SessionStart": ["hooks/session_start.py", "pipeline/supervisor.py\" --ensure-running",
                             "hooks/main_thread_rules.py\" --index"],
            "PreToolUse": ["pipeline/pretool_gate.py", "pipeline/agent_gate.py"],
            "Stop": ["pipeline/stop_gate.py"],
            "UserPromptSubmit": ["hooks/needs_you_reminder.py"],
            "SubagentStart": ["memory/inject.py", "hooks/inject_rules.py\" --index",
                              "pipeline/handoff.py\" --inject"],
            "SubagentStop": ["hooks/subagent_stop.py"],
            "PreCompact": ["pipeline/state.py\" --resume"],
            "PostToolUse": ["hooks/refusal_notice.py", "hooks/dangerous_patterns.py", "rm -f nul NUL"],
        }
        self.assertEqual(set(expected), set(by_event))
        for event, needles in expected.items():
            for needle in needles:
                with self.subTest(event=event, needle=needle):
                    self.assertTrue(any(needle in c for c in by_event[event]), by_event[event])

    def test_the_plugin_level_gate_entries_carry_no_profile_argument(self):
        # The profile is read from the agent's own definition (task-0038 S4).
        for event, matcher, hook in registered():
            if "agent_gate.py" in hook["command"]:
                self.assertEqual("PreToolUse", event)
                self.assertEqual("Bash|Edit|Write|MultiEdit|NotebookEdit|WebSearch|WebFetch",
                                 matcher)
                self.assertNotIn("--profile", hook["command"])
            if "dangerous_patterns.py" in hook["command"]:
                self.assertEqual("PostToolUse", event)
                self.assertEqual("Edit|Write|MultiEdit|NotebookEdit", matcher)
                self.assertNotIn("--profile", hook["command"])


if __name__ == "__main__":
    unittest.main()

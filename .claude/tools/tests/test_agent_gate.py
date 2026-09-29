"""Agent profiles cannot SET the approvals level or the workflow mode (task-0097).

`approvals.py <level>` and `mode.py <mode>` write `.agentry/state/approvals` and
`.agentry/state/mode`. The approvals level decides which checkpoints clear
without the CEO, so it is the same privilege boundary as a recorded approval.
Only the approval script was gated: a `docs`, `readonly` or `dev` agent that ran
either setter through Bash got exit 0, even though task-0075 already denies the
docs profile a Write to the same files.

These tests pin the repair: every agent profile is denied SETTING either value,
in the spellings a real command takes, reading stays open, and the orchestrator
main thread (which runs pretool_gate, never agent_gate) may still set both.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
import state
import tmproot  # noqa: F401  (arms the in-project temp redirect)

PROFILES = ("handle_dev", "handle_readonly", "handle_docs")
PIPE = ".claude/tools/pipeline"

# A level-setting call and a mode-setting call, in the spellings a real command
# takes: interpreter name, relative and absolute path, quoted backslashes, a
# leading `cd`, env prefix, nested shells, `-m`, and xargs feeding the argument.
APPROVALS_SET = (
    f"python {PIPE}/approvals.py auto",
    f"python3 {PIPE}/approvals.py assisted",
    f"py {PIPE}/approvals.py manual",
    f"py -3 E:/Personal/Agenty/{PIPE}/approvals.py auto",
    f"python {PIPE}/approvals.py --auto",
    f"python {PIPE}/approvals.py AUTO",
    'python ".claude\\tools\\pipeline\\approvals.py" auto',
    "python 'E:\\Personal\\Agenty\\.claude\\tools\\pipeline\\approvals.py' auto",
    "cd .claude/tools/pipeline && python approvals.py auto",
    "cd .claude/tools/pipeline && python3 -m approvals auto",
    f"env python {PIPE}/approvals.py auto",
    f"PIPELINE_LANE=x python {PIPE}/approvals.py auto",
    f"bash -c 'python {PIPE}/approvals.py auto'",
    f"sh -c \"bash -c 'python {PIPE}/approvals.py auto'\"",
    f"echo auto | xargs python {PIPE}/approvals.py",
    f"echo ok; python {PIPE}/approvals.py auto",
    # The script reaches the interpreter through a pipe or `<`: its own arguments
    # are then not the point, the interpreter runs whatever it is fed.
    f"cat {PIPE}/approvals.py | python - auto",
    # A read piped into a non-interpreter earns no credit toward a later real pipe.
    f"python {PIPE}/approvals.py --show | cat {PIPE}/approvals.py | python - auto",
    f"python {PIPE}/approvals.py --show | cat ; cat {PIPE}/approvals.py | python - auto",
    f"python - auto < {PIPE}/approvals.py",
    f"python - < {PIPE}/approvals.py",
    "cd .claude/tools/pipeline && python - auto <approvals.py",
    # Redirect tokens and a closing substitution are not arguments, and do not hide one.
    f"python {PIPE}/approvals.py 2>&1 auto",
    f"python {PIPE}/approvals.py auto 2>&1",
    f"python {PIPE}/approvals.py 1>&2 auto",
    f"python {PIPE}/approvals.py &> out.txt auto",
    f"python {PIPE}/approvals.py >& out.txt auto",
    f"python {PIPE}/approvals.py > out.txt auto",
    f"python {PIPE}/approvals.py auto > out.txt",
    f"level=$(python {PIPE}/approvals.py auto)",
    f"level=`python {PIPE}/approvals.py auto`",
)
MODE_SET = (
    f"python {PIPE}/mode.py build",
    f"python3 {PIPE}/mode.py talk",
    f"py {PIPE}/mode.py plan",
    f"py -3 E:/Personal/Agenty/{PIPE}/mode.py talk",
    f"python {PIPE}/mode.py --plan",
    'python ".claude\\tools\\pipeline\\mode.py" talk',
    "cd .claude/tools/pipeline && python mode.py talk",
    "cd .claude/tools/pipeline && python3 -m mode talk",
    f"env python {PIPE}/mode.py talk",
    f"bash -c 'python {PIPE}/mode.py talk'",
    f"sh -c \"bash -c 'python {PIPE}/mode.py talk'\"",
    f"echo talk | xargs python {PIPE}/mode.py",
    f"cat {PIPE}/mode.py | python - talk",
    f"python {PIPE}/mode.py --show | cat {PIPE}/mode.py | python - talk",
    f"python {PIPE}/mode.py --show | cat ; cat {PIPE}/mode.py | python - talk",
    f"python - talk < {PIPE}/mode.py",
    f"python - < {PIPE}/mode.py",
    "cd .claude/tools/pipeline && python - talk <mode.py",
    f"python {PIPE}/mode.py 2>&1 talk",
    f"python {PIPE}/mode.py talk 2>&1",
    f"python {PIPE}/mode.py > out.txt talk",
    f"python {PIPE}/mode.py talk > out.txt",
    f"mode=$(python {PIPE}/mode.py talk)",
    f"mode=`python {PIPE}/mode.py talk`",
)

# Reading: the no-argument form and --show / show, for both scripts.
READS = (
    f"python {PIPE}/approvals.py",
    f"python {PIPE}/approvals.py --show",
    f"python {PIPE}/approvals.py show",
    f"python {PIPE}/mode.py",
    f"python {PIPE}/mode.py --show",
    f"python {PIPE}/mode.py show",
    "cd .claude/tools/pipeline && python approvals.py --show",
    "cd .claude/tools/pipeline && python mode.py --show",
    f"bash -c 'python {PIPE}/approvals.py --show'",
    f"py -3 E:/Personal/Agenty/{PIPE}/mode.py --show",
    # Stream redirects and a substitution's closing `)` / backtick are not arguments.
    f"python {PIPE}/approvals.py 2>&1",
    f"python {PIPE}/approvals.py --show 2>&1",
    f"python {PIPE}/mode.py 2>&1",
    f"python {PIPE}/mode.py --show 2>&1",
    f"level=$(python {PIPE}/approvals.py --show)",
    f"level=`python {PIPE}/approvals.py --show`",
    f"level=$(python {PIPE}/approvals.py)",
    f"mode=$(python {PIPE}/mode.py --show)",
    f"mode=`python {PIPE}/mode.py --show`",
    f"mode=$(python {PIPE}/mode.py)",
    # A read piped into an interpreter: the interpreter runs its OWN script or
    # module, not the producing command's script.
    f"python {PIPE}/approvals.py --show | python -c 'import sys'",
    f"python {PIPE}/approvals.py --show | python -m json.tool",
    f"python {PIPE}/mode.py --show | python -c 'import sys'",
    f"python {PIPE}/mode.py --show | python -m json.tool",
)

# Reads whose output goes to a file. The readonly and docs profiles deny the
# redirect itself (a file write), which is a different rule, so these are checked
# against the setter test and the dev profile only.
REDIRECTED_READS = (
    f"python {PIPE}/approvals.py > tmp/level.txt",
    f"python {PIPE}/approvals.py --show >> tmp/level.txt",
    f"python {PIPE}/approvals.py --show >tmp/level.txt",
    f"python {PIPE}/mode.py > tmp/mode.txt",
    f"python {PIPE}/mode.py --show 2> tmp/err.txt",
    f"python {PIPE}/mode.py --show > tmp/mode.txt 2>&1",
    f"python {PIPE}/mode.py --show &> tmp/mode.txt",
    f"python {PIPE}/approvals.py >& tmp/level.txt",
)

# The state files a dev agent must not write with Write or Edit (task-0075 keeps
# the docs profile out of them already).
STATE_FILES = (
    ".agentry/state/approvals",
    ".agentry/state/mode",
    "E:/Personal/Agenty/.agentry/state/approvals",
    "E:\\Personal\\Agenty\\.agentry\\state\\mode",
    ".agentry/state./mode",
    "docs/../.agentry/state/approvals",
)

# Naming the script without running it stays allowed, as it does for the
# approval script (task-0078).
NAMED_NOT_RUN = (
    f"git show HEAD -- {PIPE}/approvals.py",
    f"cat {PIPE}/mode.py",
    f"grep -n level {PIPE}/approvals.py",
    f"cat {PIPE}/approvals.py {PIPE}/mode.py",
    f"git diff main -- {PIPE}/mode.py {PIPE}/approvals.py",
    f"grep -n auto {PIPE}/approvals.py {PIPE}/mode.py",
)


def run_profile(name: str, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = getattr(agent_gate, name)("Bash", {"command": command}, cwd=".")
    return code, err.getvalue()


class SettingStateIsOrchestratorOnlyTest(unittest.TestCase):

    def _assert_denied(self, commands: tuple, script: str):
        for name in PROFILES:
            for command in commands:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(2, code)
                    self.assertIn(f"{script} is orchestrator-only", err)

    def test_every_profile_is_denied_setting_the_approvals_level(self):
        self._assert_denied(APPROVALS_SET, "approvals.py")

    def test_every_profile_is_denied_setting_the_workflow_mode(self):
        self._assert_denied(MODE_SET, "mode.py")

    def test_every_profile_may_still_read_the_level_and_the_mode(self):
        for name in PROFILES:
            for command in READS:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(0, code, err)

    def test_naming_a_setter_script_without_running_it_stays_allowed(self):
        for name in PROFILES:
            for command in NAMED_NOT_RUN:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(0, code, err)

    def test_reads_whose_output_is_redirected_to_a_file_are_not_setting(self):
        for command in REDIRECTED_READS:
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.sets_pipeline_state(command))
                code, err = run_profile("handle_dev", command)
                self.assertEqual(0, code, err)

    def test_an_ampersand_glued_to_a_redirect_is_kept_only_where_bash_has_the_operator(self):
        # `&>`, `>&` and `<&` are redirect operators; `&<` is not, so there the
        # `&` is a real background separator and must survive.
        for command in ("x 2>&1 y", "x &>f y", "x >&f y", "x <&3 y"):
            with self.subTest(command=command):
                self.assertNotIn("&", agent_gate.AMP_REDIRECT_RE.sub("", command))
        self.assertEqual("x &<f y", agent_gate.AMP_REDIRECT_RE.sub("", "x &<f y"))

    def test_an_ampersand_less_than_still_splits_the_command_in_two(self):
        # Asserted on the split itself, not on the verdict: the second command
        # here starts with a redirect (`<x python ...`), and a redirect ahead of
        # the script is a separate gap (task-0100) that would allow it anyway.
        command = f"python {PIPE}/mode.py --show &<x python {PIPE}/mode.py talk"
        segments = pretool_gate.command_segments(agent_gate.AMP_REDIRECT_RE.sub("", command))
        self.assertEqual(2, len(segments))
        self.assertEqual(["python", f"{PIPE}/mode.py", "--show"], segments[0])

    def test_a_script_nested_past_the_depth_limit_is_refused_not_waved_through(self):
        # The innermost command is a plain read, so only the depth ceiling can
        # refuse it: whatever the gate could not scan, it must not allow.
        inner = f"python {PIPE}/approvals.py --show"
        within, past = inner, inner
        for _ in range(pretool_gate.MAX_SHELL_DEPTH):
            within = f"bash -c {shlex.quote(within)}"
        for _ in range(pretool_gate.MAX_SHELL_DEPTH + 1):
            past = f"bash -c {shlex.quote(past)}"
        self.assertEqual("", agent_gate.sets_pipeline_state(within))
        self.assertTrue(agent_gate.sets_pipeline_state(past))

    def test_text_that_cannot_be_tokenised_is_refused_not_guessed_at(self):
        # An unbalanced quote hides which command runs, so the check itself
        # refuses (the handlers refuse it earlier, on the approval-script check).
        self.assertTrue(agent_gate.sets_pipeline_state(f"python {PIPE}/mode.py 'talk"))

    def test_the_orchestrator_main_thread_may_still_set_both(self):
        for command in (f"python {PIPE}/approvals.py auto", f"python {PIPE}/mode.py talk",
                        f"python {PIPE}/approvals.py --show", f"python {PIPE}/mode.py --show"):
            with self.subTest(command=command):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = pretool_gate.handle_bash(command, cwd=".", orch=True)
                self.assertEqual(0, code, err.getvalue())


class DevProfileCannotWriteStateFilesTest(unittest.TestCase):
    """The dev profile reached pretool_gate.handle_edit, which allows anything
    under .agentry/ (bookkeeping), so a dev Write or Edit could set the approvals
    level or the mode without going near approvals.py."""

    @staticmethod
    def _edit(tool: str, path: str) -> tuple[int, str]:
        field = "content" if tool == "Write" else "new_string"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = agent_gate.handle_dev(tool, {"file_path": path, field: "auto"}, cwd=".")
        return code, err.getvalue()

    def test_dev_write_and_edit_are_denied_on_the_approvals_and_mode_files(self):
        for tool in ("Write", "Edit"):
            for path in STATE_FILES:
                with self.subTest(tool=tool, path=path):
                    code, err = self._edit(tool, path)
                    self.assertEqual(2, code)
                    self.assertIn("orchestrator", err)

    def test_dev_may_still_write_the_rest_of_agentry(self):
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                code, err = self._edit(tool, ".agentry/tasks/active/task-0001.md")
                self.assertEqual(0, code, err)


class DocsProfileWritePathsTest(unittest.TestCase):
    """The docs profile writes the work product AND the harness config.

    FR-13 moved tasks, handoffs, specs and plans to .agentry/ while rules/,
    skills/ and agents/ stayed in .claude/. is_docs_path() kept only the old
    tree, so every docs agent was denied its own handoff doc - which then raised
    handoff debt and froze edits tree-wide."""

    def test_the_work_product_tree_is_writable(self):
        for path in (".agentry/tasks/handoffs/task-0008.md",
                     ".agentry/specs/spec-0001.md",
                     ".agentry/tasks/backlog/task-0010.md",
                     r".agentry\tasks\handoffs\task-0008.md"):
            with self.subTest(path=path):
                self.assertEqual(0, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_the_harness_tree_is_still_writable(self):
        # The regression the fix could introduce: .agentry/ ADDED, not substituted.
        for path in (".claude/rules/git-workflow.md",
                     ".claude/skills/new-task/SKILL.md",
                     ".claude/agents/reviewer.md",
                     "docs/technical/architecture.md",
                     "README.md"):
            with self.subTest(path=path):
                self.assertEqual(0, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_application_source_outside_both_trees_is_denied(self):
        for path in ("src/services/OrderService.php", "app/main.py",
                     "frontend/src/App.tsx"):
            with self.subTest(path=path):
                self.assertEqual(2, agent_gate.handle_docs("Edit", {"file_path": path}))


def run_agent_gate(profile: str, tool: str, path: str) -> subprocess.CompletedProcess:
    """Feed the real hook a payload on stdin, exactly as Claude Code does."""
    payload = {"tool_name": tool, "tool_input": {"file_path": path}, "cwd": str(state.ROOT)}
    return subprocess.run(
        [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), "--profile", profile],
        input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=30)


class DocsProfileRootClaudeMdTest(unittest.TestCase):
    """task-0075: the technical-writer owns the root CLAUDE.md and was denied it,
    while the orchestrator gate allowed it. The fix must ADD that one file, not
    substitute it for an existing allowance and not widen the profile."""

    def setUp(self):
        self.root_claude = str(state.ROOT / "CLAUDE.md")

    def test_docs_write_to_root_claude_md_is_allowed_by_the_hook(self):
        proc = run_agent_gate("docs", "Write", self.root_claude)
        self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_docs_root_claude_md_is_allowed_in_every_spelling(self):
        for path in (self.root_claude, self.root_claude.replace("\\", "/"),
                     "CLAUDE.md"):
            with self.subTest(path=path):
                self.assertEqual(0, agent_gate.handle_docs("Edit", {"file_path": path}))

    def test_a_claude_md_below_the_root_is_still_denied(self):
        # Root only, matching pretool_gate.orch_allowed_path(): a file that merely
        # has the same name inside an application tree is not the root document.
        for path in (str(state.ROOT / "src" / "app" / "CLAUDE.md"),
                     str(state.ROOT / "CLAUDE.md.py")):
            with self.subTest(path=path):
                self.assertEqual(2, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_every_earlier_documentation_path_is_still_allowed(self):
        # The trap this function is known for: one allowance added, another lost.
        for path in (".claude/CLAUDE.md", "README.md", "README.rst", "docs/technical/a.md",
                     ".claude/rules/testing.md", ".agentry/tasks/handoffs/task-0075.md",
                     str(state.ROOT / ".claude" / "CLAUDE.md"),
                     str(state.ROOT / "README.rst")):
            with self.subTest(path=path):
                self.assertEqual(0, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_application_source_is_still_denied_to_docs(self):
        for path in ("src/app/main.py", str(state.ROOT / "app.py"),
                     "docs/../src/app/main.py", "readme_generator.py"):
            with self.subTest(path=path):
                self.assertEqual(2, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_readonly_write_to_root_claude_md_is_still_denied(self):
        proc = run_agent_gate("readonly", "Write", self.root_claude)
        self.assertEqual(2, proc.returncode)
        self.assertEqual(2, agent_gate.handle_readonly("Edit", {"file_path": self.root_claude}))


class DocsProfileProtectedStateTest(unittest.TestCase):
    """task-0075 (absorbed 0050 / FR-8): is_docs_path() accepts all of .agentry/,
    and the approvals level, workflow mode, run.db approval flags, solo trunk_push
    approval and memory stamps live in .agentry/state/, so a docs agent could set
    what clears without the CEO. The whole state tree is denied."""

    PROTECTED = (".agentry/state/approvals", ".agentry/state/mode",
                 ".agentry/state/trunk_push", ".agentry/state/run.db",
                 ".agentry/state/memory/task-0001.json")

    def test_the_hook_denies_a_docs_write_to_anything_under_agentry_state(self):
        for rel in self.PROTECTED:
            path = str(state.ROOT / rel)
            with self.subTest(path=path):
                proc = run_agent_gate("docs", "Write", path)
                self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_lane_suffixed_files_are_protected_too(self):
        # mode.py / approvals.py add state.LANE_SUFFIX when PIPELINE_LANE is set.
        for path in (".agentry/state/approvals.plan", ".agentry/state/mode.build_2",
                     ".agentry/state/Approvals.Lane-1"):
            with self.subTest(path=path):
                self.assertEqual(2, agent_gate.handle_docs("Write", {"file_path": path}))

    def test_every_spelling_of_the_path_is_protected(self):
        for path in (r".agentry\state\approvals", ".AGENTRY/STATE/MODE",
                     str(state.ROOT / ".agentry" / "state" / "approvals"),
                     ".agentry/tasks/../state/approvals",
                     "docs/../.agentry/state/mode",
                     ".agentry/state/approvals.",
                     ".agentry/state/trunk_push", r".agentry\state\RUN.DB",
                     ".agentry/state/memory/task-0001.json",
                     str(state.ROOT / ".agentry" / "state" / "trunk_push"),
                     # Windows resolves a trailing dot or space on the directory
                     # segment to the same directory (measured).
                     ".agentry/state./trunk_push", ".agentry/state /run.db",
                     ".agentry/state"):
            with self.subTest(path=path):
                self.assertEqual(2, agent_gate.handle_docs("Edit", {"file_path": path}))

    def test_the_deny_names_the_real_reason(self):
        proc = run_agent_gate("docs", "Write", ".agentry/state/trunk_push")
        stderr = proc.stderr.decode("utf-8", "replace")
        self.assertIn("is under .agentry/state/", stderr)
        self.assertIn("Report the level or mode change you need to the orchestrator", stderr)
        self.assertNotIn("outside that scope", stderr)
        self.assertNotIn("approvals.py", stderr)

    def test_the_rest_of_agentry_stays_writable_to_docs(self):
        # Scoped to the state directory: siblings and files that merely carry the
        # word are still documentation.
        for path in (".agentry/tasks/active/task-0075.md", ".agentry/tasks/approvals.md",
                     ".agentry/plans/mode.md", ".agentry/specs/spec-0001.md",
                     ".agentry/statement.md", ".agentry/state-of-play.md",
                     ".agentry/plans/state/design.md"):
            with self.subTest(path=path):
                self.assertEqual(0, agent_gate.handle_docs("Write", {"file_path": path}))


if __name__ == "__main__":
    unittest.main()

"""pretool_gate security fixes of task-0120 (SEC-B): H2, M1 (trunk marker), M3, and the
glued-closer marking.

  * H2: an exception while a SUBAGENT call is checked denies it; the main thread stays
    fail-open on purpose, so a bug in the gate cannot brick the orchestrator.
  * M1: a subagent never pushes the trunk (the marker is the orchestrator's and is left
    unspent), and the orchestrator's spend is an atomic rename that must succeed before
    the push is allowed.
  * M3: the live `.claude/rules`, `skills`, `commands` and anything inside a `.git`
    directory are out of reach of every subagent writer; `src/.claude/**` is not.
  * The closer of an unquoted substitution is marked whether or not it held a separator,
    so `$(git push origin feature/task-1)` reads as the feature branch it pushes.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
from test_allow_push_to_main import flag
from test_pretool_gate_git import denial_reason, gate_state
from test_subagent_enforcement import make_project
from test_trunk_push_by_ref import approve_trunk, task_branch_checked_out

SUBAGENT_KEYS = ({"agent_id": "a3d9c94df3116b37f"}, {"agent_type": "senior-backend-dev"},
                 {"transcript_path": "C:/s/sess/subagents/agent-a1.jsonl"})


def run_evaluate(payload: dict) -> tuple[int, str]:
    """pretool_gate.evaluate() fed `payload` on stdin: (exit code, stderr)."""
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
    err = io.StringIO()
    with unittest.mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stderr(err):
        return pretool_gate.evaluate(), err.getvalue()


def boom(*_args, **_kwargs):
    raise RuntimeError("injected")


class SubagentFailsClosedTest(unittest.TestCase):
    """H2: the outer handler used to answer allow() for every exception."""

    def bash(self, extra: dict) -> dict:
        return {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "", **extra}

    def edit(self, extra: dict) -> dict:
        return {"tool_name": "Write", "tool_input": {"file_path": "app/a.py", "content": "x"},
                "cwd": "", **extra}

    def test_an_exception_in_the_bash_path_denies_every_subagent_spelling(self):
        for extra in SUBAGENT_KEYS:
            with self.subTest(identity=extra), \
                    unittest.mock.patch.object(pretool_gate, "check_branch_creation", boom):
                code, err = run_evaluate(self.bash(extra))
                self.assertEqual(2, code, err)
                self.assertIn("RuntimeError", err)

    def test_an_exception_in_the_edit_path_denies_every_subagent_spelling(self):
        for extra in SUBAGENT_KEYS:
            with self.subTest(identity=extra), \
                    unittest.mock.patch.object(pretool_gate, "is_live_harness", boom):
                code, err = run_evaluate(self.edit(extra))
                self.assertEqual(2, code, err)

    def test_an_exception_in_a_late_step_denies_too(self):
        with unittest.mock.patch.object(pretool_gate, "plan_bash_write", boom):
            code, err = run_evaluate(self.bash(SUBAGENT_KEYS[0]))
        self.assertEqual(2, code, err)

    def test_the_main_thread_stays_fail_open(self):
        with unittest.mock.patch.object(pretool_gate, "check_branch_creation", boom):
            code, err = run_evaluate(self.bash({}))
        self.assertEqual(0, code, err)
        with unittest.mock.patch.object(pretool_gate, "handle_edit", boom):
            code, err = run_evaluate(self.edit({}))
        self.assertEqual(0, code, err)

    def test_a_payload_that_is_not_an_object_is_not_a_subagent_call(self):
        stdin = io.TextIOWrapper(io.BytesIO(b"[1, 2]"), encoding="utf-8")
        with unittest.mock.patch.object(sys, "stdin", stdin):
            self.assertEqual(0, pretool_gate.evaluate())

    def test_a_clean_subagent_call_is_unaffected(self):
        code, err = run_evaluate(self.bash(SUBAGENT_KEYS[0]))
        self.assertEqual(0, code, err)


class SubagentNeverPushesTheTrunkTest(unittest.TestCase):
    """M1: the marker is not bound to a caller."""

    def test_a_subagent_trunk_push_is_denied_and_the_marker_stays_unspent(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            before = marker.read_text(encoding="utf-8")
            code, err = denial_reason("git push origin main", repo.path, orch=False)
            self.assertEqual(2, code, err)
            self.assertIn("A subagent never pushes the trunk", err)
            self.assertTrue(marker.exists())
            self.assertEqual(before, marker.read_text(encoding="utf-8"))

    def test_every_trunk_spelling_is_denied_for_a_subagent(self):
        for command in ("git push origin main:main", "git push -u origin refs/heads/main",
                        "git push origin HEAD:main", "git push origin main bugfix/task-9111"):
            with self.subTest(command=command), task_branch_checked_out({}) as repo:
                marker = approve_trunk()
                code, err = denial_reason(command, repo.path, orch=False)
                self.assertEqual(2, code, err)
                self.assertTrue(marker.exists())

    def test_the_allow_push_to_main_flag_does_not_open_it_for_a_subagent(self):
        with task_branch_checked_out({}) as repo, flag(True):
            code, err = denial_reason("git push origin main", repo.path, orch=False)
            self.assertEqual(2, code, err)
            code, err = denial_reason("git push origin main", repo.path, orch=True)
            self.assertEqual(0, code, err)

    def test_the_same_push_is_allowed_for_the_orchestrator_and_spends_the_marker(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            code, err = denial_reason("git push origin main", repo.path, orch=True)
            self.assertEqual(0, code, err)
            self.assertFalse(marker.exists())

    def test_a_subagent_work_branch_push_is_untouched(self):
        with task_branch_checked_out({"task-9111": {"push_approved": 1}}) as repo:
            code, err = denial_reason("git push origin bugfix/task-9111", repo.path, orch=False)
            self.assertEqual(0, code, err)

    def test_the_hook_entry_classes_a_subagent_payload_as_non_orch(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            payload = {"tool_name": "Bash", "cwd": str(repo.path), "agent_id": "a1",
                       "tool_input": {"command": "git push origin main"}}
            code, err = run_evaluate(payload)
            self.assertEqual(2, code, err)
            self.assertTrue(marker.exists())


class MarkerSpendIsAtomicTest(unittest.TestCase):
    """M1: unlink swallowed OSError, so a failed spend left the approval standing."""

    def test_the_spend_renames_the_marker_to_a_unique_spent_name(self):
        with gate_state(mode="solo"):
            marker = approve_trunk()
            calls = []
            real = os.replace

            def spy(src, dst):
                calls.append((Path(src), Path(dst)))
                return real(src, dst)

            with unittest.mock.patch.object(pretool_gate.os, "replace", spy):
                self.assertTrue(pretool_gate.consume_trunk_push_marker())
            self.assertEqual(1, len(calls))
            src, dst = calls[0]
            self.assertEqual(marker, src)
            self.assertEqual(marker.parent, dst.parent)
            self.assertTrue(dst.name.startswith(marker.name + ".spent-"), dst.name)
            self.assertFalse(marker.exists())

    def test_two_spends_never_both_win(self):
        with gate_state(mode="solo"):
            approve_trunk()
            self.assertTrue(pretool_gate.consume_trunk_push_marker())
            self.assertFalse(pretool_gate.consume_trunk_push_marker())

    def test_two_spends_use_different_spent_names(self):
        with gate_state(mode="solo"):
            names = []
            real = os.replace

            def spy(src, dst):
                names.append(Path(dst).name)
                return real(src, dst)

            with unittest.mock.patch.object(pretool_gate.os, "replace", spy):
                for _ in range(2):
                    approve_trunk()
                    pretool_gate.consume_trunk_push_marker()
            self.assertEqual(2, len(set(names)))

    def test_a_failed_replace_denies_the_push_and_leaves_the_marker(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            with unittest.mock.patch.object(pretool_gate.os, "replace", boom_os):
                code, err = denial_reason("git push origin main", repo.path, orch=True)
            self.assertEqual(2, code, err)
            self.assertIn("could not be spent", err)
            self.assertTrue(marker.exists())

    def test_a_failed_tidy_unlink_does_not_matter_once_the_marker_is_renamed(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            with unittest.mock.patch.object(Path, "unlink", boom_os):
                code, err = denial_reason("git push origin main", repo.path, orch=True)
            self.assertEqual(0, code, err)
            self.assertFalse(marker.exists())

    def test_a_marker_spent_by_a_racing_push_denies_this_one(self):
        with task_branch_checked_out({}) as repo:
            marker = approve_trunk()
            real = pretool_gate.consume_trunk_push_marker

            def lose_the_race():
                marker.unlink()  # the other push took it between our check and our spend
                return real()

            with unittest.mock.patch.object(pretool_gate, "consume_trunk_push_marker",
                                            lose_the_race):
                code, err = denial_reason("git push origin main", repo.path, orch=True)
            self.assertEqual(2, code, err)
            self.assertIn("could not be spent", err)


def boom_os(*_args, **_kwargs):
    raise OSError("injected")


class LiveHarnessWiderSetTest(unittest.TestCase):
    """M3: rules, skills, commands and .git, for every subagent writer."""

    def setUp(self):
        self.proj = make_project(self)
        self.cwd = str(self.proj)

    LIVE = (".claude/rules/testing.md", ".claude/rules/sub/x.md", ".claude/skills/new-task/SKILL.md",
            ".claude/commands/x.md", ".claude/rules", ".claude/tools/pipeline/x.py",
            ".git/hooks/pre-commit", ".git/config", ".git/info/attributes", ".git",
            "src/.git/hooks/pre-push", "src/.git/config", "app/.git/hooks/post-merge",
            "docs/deep/er/.git/config")
    NOT_LIVE = ("src/.claude/rules/testing.md", "src/.claude/skills/new-task/SKILL.md",
                "src/.claude/commands/x.md", "src/.claude/tools/pipeline/x.py",
                ".claude/CLAUDE.md", ".claude/rulebook.md", ".claude/skills-notes.md",
                ".gitignore", ".gitattributes", ".github/workflows/ci.yml", "docs/git/x",
                "app/a.git/x", "app/.gitmodules", "docs/a.md", ".agentry/tasks/backlog/t.md")

    def test_the_wider_set_is_recognised_in_every_spelling(self):
        for rel in self.LIVE:
            for path in (rel, str(self.proj / rel), rel.upper(), rel + "::$DATA",
                         "docs/../" + rel, rel.replace("/", "\\")):
                with self.subTest(path=path):
                    self.assertTrue(pretool_gate.is_live_harness(path, self.cwd), path)

    def test_a_git_dir_is_live_outside_the_project_root_too(self):
        outside = Path(self.proj).parent / "elsewhere" / ".git" / "hooks" / "pre-commit"
        self.assertTrue(pretool_gate.is_live_harness(str(outside), self.cwd))

    def test_ntfs_trailing_dot_or_space_does_not_hide_a_git_dir(self):
        for path in (".git./hooks/x", ".git /hooks/x", "app/.git../config"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.is_live_harness(path, self.cwd), path)

    def test_the_src_checkout_and_neighbours_stay_writable(self):
        for path in self.NOT_LIVE:
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_live_harness(path, self.cwd), path)

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        self.assertTrue(pretool_gate.is_live_harness("x.md", str(self.proj / ".claude" / "rules")))
        self.assertFalse(pretool_gate.is_live_harness(
            "x.md", str(self.proj / "src" / ".claude" / "rules")))
        self.assertTrue(pretool_gate.is_live_harness("hooks/pre-push", str(self.proj / "src" / ".git")))

    def test_every_edit_tool_is_denied_for_a_subagent(self):
        with gate_state():
            for rel in self.LIVE:
                for tool, content in (("Write", "x"), ("Edit", "x")):
                    with self.subTest(tool=tool, path=rel):
                        err = io.StringIO()
                        with contextlib.redirect_stderr(err):
                            code = pretool_gate.handle_edit(rel, content, orch=False, cwd=self.cwd)
                        self.assertEqual(2, code, err.getvalue())
                        self.assertIn("live harness", err.getvalue())

    def test_a_subagent_may_write_the_src_checkout(self):
        with gate_state():
            for rel in self.NOT_LIVE[:4]:
                with self.subTest(path=rel):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        pretool_gate.handle_edit(rel, "x", orch=False, cwd=self.cwd)
                    self.assertNotIn("live harness", err.getvalue())

    def test_the_orchestrator_may_still_edit_live_rules(self):
        # The deploy path: the orchestrator allowlist governs it, not the live-harness deny.
        with gate_state():
            for rel in (".claude/rules/testing.md", ".claude/skills/new-task/SKILL.md"):
                with self.subTest(path=rel):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        code = pretool_gate.handle_edit(rel, "x", orch=True, cwd=self.cwd)
                    self.assertEqual(0, code, err.getvalue())


class GluedCloserMarkingTest(unittest.TestCase):
    """The substitution closer is marked whether or not the substitution held a separator."""

    MARK = pretool_gate.GLUED_CLOSE

    def test_an_unquoted_closer_is_marked_with_or_without_a_separator(self):
        for command in ("x=$( git push origin feature/task-1)", "echo ` git push origin feature/task-1`",
                        "x=$(echo a; git push origin feature/task-1)/foo",
                        "x=$( git push origin feature/task-1)/foo"):
            with self.subTest(command=command):
                self.assertIn(self.MARK, pretool_gate.pad_separators(command))

    def test_a_substitution_that_pushes_nothing_is_not_rewritten(self):
        for command in ("echo $(date)", "echo $((1+2))", "cat <(ls)", "tee >(cat)", "a=(x y)",
                        "diff <(git status) <(echo x)", "echo `date`", "x=$(git log --oneline)"):
            with self.subTest(command=command):
                self.assertEqual(command, pretool_gate.pad_separators(command))

    def test_a_quoted_or_escaped_closer_is_never_marked(self):
        for command in ("git push origin 'x)y:main'", 'git push origin "x)y:main"',
                        "git push origin x\\)y:main", "git push origin 'x`y:main'"):
            with self.subTest(command=command):
                self.assertNotIn(self.MARK, pretool_gate.pad_separators(command))

    def test_the_control_character_cannot_be_forged_from_the_command(self):
        padded = pretool_gate.pad_separators("git push origin 'x" + self.MARK + "y:main'")
        self.assertNotIn(self.MARK, padded)

    def test_a_substitution_push_to_a_work_branch_resolves_to_that_branch(self):
        for command in ("x=$( git push origin feature/task-1)", "echo ` git push origin feature/task-1`",
                        "out=$( git push -u origin feature/task-1) && echo done",
                        "echo $( git push origin feature/task-1)/foo",
                        "echo $( git push origin feature/task-1 )"):
            with self.subTest(command=command):
                pushes = [a for sub, a in pretool_gate.git_invocations(command) if sub == "push"]
                self.assertEqual(1, len(pushes))
                self.assertIsNone(pretool_gate.push_ref_with_closer(pushes[0]))
                self.assertEqual([("origin", "feature/task-1", "feature/task-1")],
                                 pretool_gate.push_remote_pairs(command, "main", []))

    def test_a_substitution_push_to_the_trunk_is_still_the_trunk(self):
        for command in ("x=$( git push origin main)", "echo ` git push origin main`",
                        "x=$( git push origin main)/foo"):
            with self.subTest(command=command):
                pairs = pretool_gate.push_remote_pairs(command, "main", [])
                self.assertEqual([("origin", "main", "main")], pairs)

    def test_a_quoted_closer_inside_a_substitution_stays_refused(self):
        for command in ("echo $( git push origin 'x)y:main')",
                        "echo $( git push origin 'x)y:feature/task-1')",
                        "echo $( git push origin \"x)y:main\")", "echo ` git push origin 'x`y:main'`"):
            with self.subTest(command=command):
                self.assertIsNone(pretool_gate.push_remote_pairs(command, "main", []))

    def test_the_marker_survives_in_front_of_a_closer_in_a_longer_command(self):
        command = "x=$( git push origin feature/task-1); git status"
        self.assertEqual([("origin", "feature/task-1", "feature/task-1")],
                         pretool_gate.push_remote_pairs(command, "main", []))


if __name__ == "__main__":
    unittest.main()

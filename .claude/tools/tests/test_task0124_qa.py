"""task-0124 QA: paths the dev tests leave open.

- the push budget really denies end to end, and every git helper on the push path
  lets the expiry through instead of swallowing it;
- the pause reason is bounded like the reject reason;
- the exit-gate temp log does not outlive the call, stderr is captured, odd bytes
  never crash it;
- the commit-tag branch rule, end to end, ignores a `branch:` line written after
  registration;
- run rows that predate the frozen columns, several at once;
- a UNC path never reaches a filesystem stat, and U+2028 / U+2029 do not survive
  the reason cleaner (both were expected failures until the F2 fix).
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import approve
import gate
import pretool_gate
import state
import test_event_writers as ew
import tmproot

BRANCH = "feature/task-9210"
OWNER = "task-9210"      # owns the branch, not approved
RIDER = "task-9211"      # approved, did NOT declare the branch when it registered


def git(args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


class PushBudgetEndToEndTest(unittest.TestCase):
    def setUp(self):
        sandbox = tmproot.sandbox(self, "push_budget_qa_")
        self.repo = sandbox / "repo"
        self.repo.mkdir()
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        (self.repo / "seed.txt").write_text("x", encoding="utf-8")
        git(["add", "seed.txt"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["checkout", "-q", "-b", "bugfix/task-9212"], self.repo)
        self.addCleanup(pretool_gate.end_push_budget)

    def push(self) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash("git push -u origin bugfix/task-9212", cwd=str(self.repo))
        return code, err.getvalue()

    def test_a_push_with_no_time_left_is_denied_through_real_git_calls(self):
        with unittest.mock.patch.object(pretool_gate, "PUSH_BUDGET", 0.0):
            code, err = self.push()
        self.assertEqual(2, code)
        self.assertIn("did not finish within", err)
        self.assertIsNone(pretool_gate._push_deadline)

    def test_the_same_push_with_the_normal_budget_is_not_refused_for_time(self):
        _code, err = self.push()
        self.assertNotIn("time budget", err)
        self.assertIsNone(pretool_gate._push_deadline)

    def test_a_denied_expiry_leaves_no_budget_for_the_next_command(self):
        with unittest.mock.patch.object(pretool_gate, "PUSH_BUDGET", 0.0):
            self.push()
        self.assertIsNone(pretool_gate._push_deadline)
        self.assertEqual("bugfix/task-9212", pretool_gate.current_branch("", str(self.repo)))

    def test_every_git_helper_on_the_push_path_lets_the_expiry_through(self):
        pretool_gate.start_push_budget(-1.0)
        cwd = str(self.repo)
        calls = {
            "current_branch": lambda: pretool_gate.current_branch("", cwd),
            "local_branches": lambda: pretool_gate.local_branches("", cwd),
            "push_repo": lambda: pretool_gate.push_repo("", cwd),
            "implicit_push_remote": lambda: pretool_gate.implicit_push_remote("", cwd, "x"),
        }
        for name, call in calls.items():
            with self.subTest(helper=name), self.assertRaises(pretool_gate.PushBudgetExpired):
                call()

    def test_a_budget_ending_midway_gives_the_later_call_only_what_is_left(self):
        clock = [100.0]
        seen = []
        step = pretool_gate.PUSH_BUDGET - 0.5

        def run(argv, **kwargs):
            seen.append(kwargs["timeout"])
            clock[0] += step
            return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")

        with unittest.mock.patch.object(pretool_gate.time, "monotonic", lambda: clock[0]), \
                unittest.mock.patch.object(pretool_gate.subprocess, "run", run):
            pretool_gate.start_push_budget()
            pretool_gate.git_run(["git", "a"], ".")
            pretool_gate.git_run(["git", "b"], ".")  # 0.5 s left
            with self.assertRaises(pretool_gate.PushBudgetExpired):
                pretool_gate.git_run(["git", "c"], ".")
        self.assertEqual([10, 0.5], seen)


class RunRowFreezeEndToEndTest(unittest.TestCase):
    """handle_bash with a branch line added to the task file AFTER registration."""

    def setUp(self):
        sandbox = tmproot.sandbox(self, "freeze_qa_")
        self.repo = sandbox / "repo"
        self.repo.mkdir()
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        (self.repo / "seed.txt").write_text("x", encoding="utf-8")
        git(["add", "seed.txt"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["checkout", "-q", "-b", BRANCH], self.repo)
        self.active = sandbox / "tasks" / "active"
        self.active.mkdir(parents=True)
        dirs = {"backlog": sandbox / "tasks" / "backlog", "active": self.active,
                "done": sandbox / "tasks" / "done"}
        for folder in dirs.values():
            folder.mkdir(exist_ok=True)
        for patcher in (
                unittest.mock.patch.object(state, "STATE_DIR", sandbox),
                unittest.mock.patch.object(state, "DB_PATH", sandbox / "run.db"),
                unittest.mock.patch.object(state, "load_pipeline", return_value={}),
                unittest.mock.patch.dict(state.TASK_DIRS, dirs)):
            patcher.start()
            self.addCleanup(patcher.stop)
        for task in (OWNER, RIDER):
            (self.active / f"{task}.md").write_text(f"---\nid: {task}\n---\nbody\n", encoding="utf-8")
        conn = state.connect()
        try:
            state.create_run(conn, OWNER, "feature", "ready")
            state.create_run(conn, RIDER, "feature", "ready")
            state.set_fields(conn, RIDER, commit_approved=1)
        finally:
            conn.close()

    def commit(self) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(f'git commit -m "[{RIDER}] borrow"', cwd=str(self.repo))
        return code, err.getvalue()

    def test_a_branch_line_written_after_registration_cannot_borrow_the_approval(self):
        (self.active / f"{RIDER}.md").write_text(
            f"---\nid: {RIDER}\nbranch: {BRANCH}\n---\nbody\n", encoding="utf-8")
        code, err = self.commit()
        self.assertEqual(2, code)
        self.assertIn("did not declare", err)
        self.assertIn("ask the orchestrator", err)
        self.assertNotIn("BEFORE registering", err)  # the remedy it used to name is unreachable

    def test_the_denial_tells_the_agent_the_edit_changes_nothing(self):
        code, err = self.commit()
        self.assertEqual(2, code)
        self.assertIn("frozen", err)


class PauseReasonBoundTest(ew.Harness):
    def test_a_pause_reason_is_stored_bounded_and_without_control_characters(self):
        self.seed(stage="implement")
        dirty = "preempted\nby\x1b[31m the CEO " + "z" * 2000
        with unittest.mock.patch.object(approve, "assess_pause",
                                        return_value=(approve.PAUSE_CLEAN, "")):
            code, _out = self.approve("--pause", dirty)
        self.assertEqual(0, code)
        stored = self.row()["pause_reason"]
        self.assertTrue(stored.startswith("preempted by"))
        self.assertLessEqual(len(stored), approve.REASON_MAX)
        self.assertNotIn("\n", stored)
        self.assertNotIn("\x1b", stored)

    def test_a_reason_exactly_at_the_cap_is_kept_whole(self):
        self.assertEqual("a" * approve.REASON_MAX, approve.clean_reason("a" * approve.REASON_MAX))

    def test_a_reason_one_over_the_cap_loses_only_the_excess(self):
        self.assertEqual("a" * approve.REASON_MAX, approve.clean_reason("a" * (approve.REASON_MAX + 1)))

    def test_trailing_space_left_by_the_cut_is_trimmed(self):
        text = "a" * (approve.REASON_MAX - 1) + "\nb"
        self.assertEqual("a" * (approve.REASON_MAX - 1), approve.clean_reason(text))

    def test_c1_controls_and_delete_become_spaces(self):
        self.assertEqual("a b c", approve.clean_reason("a\x85b\x7fc"))

    def test_line_and_paragraph_separators_are_removed_too(self):
        # approve.py clean_reason only maps categories Cc and Cf, so U+2028 (Zl) and
        # U+2029 (Zp) still break lines in whatever renders the echoed reason.
        cleaned = approve.clean_reason("one\u2028two\u2029three")
        self.assertNotIn("\u2028", cleaned)
        self.assertNotIn("\u2029", cleaned)


class GateRunCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmproot.sandbox(self, "gate_qa_")

    def logs(self) -> set:
        return {p.name for p in tmproot.RUN_ROOT.glob("gaterun-*.log")}

    def test_the_temp_log_is_removed_after_a_normal_run(self):
        before = self.logs()
        gate.run_command(f'"{sys.executable}" -c "print(1)"', str(self.tmp), 30)
        self.assertEqual(before, self.logs())

    def test_stderr_is_part_of_the_output_and_the_exit_code_is_kept(self):
        cmd = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'on-stderr\'); sys.exit(5)"'
        code, output = gate.run_command(cmd, str(self.tmp), 30)
        self.assertEqual(5, code)
        self.assertIn("on-stderr", output)

    def test_bytes_that_are_not_utf8_are_replaced_not_raised(self):
        cmd = (f'"{sys.executable}" -c "import sys; '
               f'sys.stdout.buffer.write(b\'ok \\xff\\xfe done\'); sys.stdout.flush()"')
        code, output = gate.run_command(cmd, str(self.tmp), 30)
        self.assertEqual(0, code)
        self.assertIn("ok", output)
        self.assertIn("done", output)

    def test_a_command_that_cannot_start_in_a_missing_directory_is_an_os_error(self):
        with self.assertRaises(OSError):
            gate.run_command("echo hi", str(self.tmp / "no-such-dir"), 30)

    def test_run_gate_reports_a_start_failure_as_127(self):
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": "echo hi", "cwd": "no-such-dir-9999"}}]}}}
        result = gate.run_gate(pipeline, "test")
        self.assertEqual(127, result["exit"])
        self.assertFalse(result["passed"])


class UncExtraSpellingsTest(unittest.TestCase):
    def test_mixed_separators_quotes_and_leading_space_are_still_unc(self):
        for path in ("\\/server\\share\\x", "/\\server/share/x", "'\\\\server\\share'",
                     '"//server/share"', "  \\\\server\\share"):
            self.assertTrue(pretool_gate.is_unc(path), path)

    def test_a_path_with_three_leading_separators_is_still_unc(self):
        self.assertTrue(pretool_gate.is_unc("///x"))

    def test_resolves_into_state_never_resolves_a_unc_form(self):
        with unittest.mock.patch.object(agent_gate.os.path, "realpath",
                                        side_effect=AssertionError("realpath on a UNC path")), \
                unittest.mock.patch.object(agent_gate.os.path, "samefile",
                                           side_effect=AssertionError("samefile on a UNC path")):
            self.assertFalse(agent_gate.resolves_into_state(r"\\server\share\run.db"))

    def test_safe_realpath_is_lexical_for_unc_and_real_for_local(self):
        with unittest.mock.patch.object(pretool_gate.os.path, "realpath",
                                        side_effect=AssertionError("resolved")):
            self.assertEqual(os.path.normpath(r"\\server\share\a\..\b"),
                             pretool_gate.safe_realpath(r"\\server\share\a\..\b"))
        self.assertEqual(os.path.realpath("."), pretool_gate.safe_realpath("."))

    def test_a_unc_glob_in_a_subagent_command_is_refused_without_expansion(self):
        with unittest.mock.patch.object(agent_gate.glob, "iglob",
                                        side_effect=AssertionError("globbed a UNC path")):
            self.assertEqual([], agent_gate.glob_hits("//server/share/*"))


class UncStatTest(unittest.TestCase):
    """handle_bash must not touch the network for a UNC repo or message path."""

    def stat_spy(self):
        seen = []
        real = os.stat

        def spy(path, *args, **kwargs):
            text = os.fsdecode(path) if isinstance(path, bytes) else str(path)
            if pretool_gate.is_unc(text):
                seen.append(text)
            return real(path, *args, **kwargs)

        return seen, spy

    def run_command(self, command: str) -> list:
        seen, spy = self.stat_spy()
        with unittest.mock.patch.object(os, "stat", spy), \
                contextlib.redirect_stderr(io.StringIO()):
            pretool_gate.handle_bash(command, cwd=str(state.ROOT))
        return seen

    def test_git_dash_c_with_a_unc_directory_is_never_statted(self):
        # pretool_gate.py: `Path(repo).is_dir()` in current_branch, local_branches,
        # push_repo and the commit bootstrap runs before any UNC check.
        self.assertEqual([], self.run_command("git -C //srv-qa/share push origin bugfix/task-9"))

    def test_git_commit_dash_f_with_a_unc_message_file_is_never_statted(self):
        # pretool_gate.py read_message_file: Path.is_file() on the named path.
        self.assertEqual([], self.run_command("git commit -F //srv-qa/share/msg.txt"))


class LegacyRowsBackfillTest(unittest.TestCase):
    def setUp(self):
        tmp = tmproot.sandbox(self, "legacy_qa_").resolve()
        self.dirs = {name: tmp / name for name in ("backlog", "active", "done")}
        for folder in self.dirs.values():
            folder.mkdir()
        for name, value in (("TASK_DIRS", self.dirs), ("STATE_DIR", tmp / "state"),
                            ("DB_PATH", tmp / "state" / "run.db")):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_old_row_is_frozen_on_the_first_connect_and_a_row_without_a_file_is_empty(self):
        (self.dirs["active"] / "task-9301.md").write_text(
            "---\nid: 1\nepic: epic-0007\nbranch: feature/task-9300\n---\nbody\n", encoding="utf-8")
        (self.dirs["done"] / "task-9302.md").write_text(
            "---\nid: 2\nepic: epic-0007\numbrella: true\nsubtasks: [task-9301]\n---\nbody\n",
            encoding="utf-8")
        state.STATE_DIR.mkdir(parents=True)
        legacy = sqlite3.connect(state.DB_PATH)
        legacy.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
                       "stage_status TEXT, awaiting_human TEXT, commit_approved INTEGER DEFAULT 0, "
                       "updated TEXT)")
        for task in ("task-9301", "task-9302", "task-9303"):
            legacy.execute("INSERT INTO runs (task, type, stage, stage_status, awaiting_human, "
                           "commit_approved) VALUES (?, 'feature', 'done', 'in_progress', '', 1)",
                           (task,))
        legacy.commit()
        legacy.close()
        conn = state.connect()
        try:
            rows = {task: state.get_run(conn, task) for task in ("task-9301", "task-9302", "task-9303")}
        finally:
            conn.close()
        old_row, umbrella_row, bare_row = rows["task-9301"], rows["task-9302"], rows["task-9303"]
        self.assertEqual(("epic-0007", "feature/task-9300", ""),
                         (old_row["epic"], old_row["branch"], old_row["umbrella"]))
        self.assertEqual(("true", "task-9301"), (umbrella_row["umbrella"], umbrella_row["subtasks"]))
        self.assertEqual(("",) * len(state.FROZEN_FIELDS),
                         tuple(bare_row[c] for c in state.FROZEN_FIELDS))
        self.assertEqual("task-9302", state.covering_umbrella("task-9301"))
        self.assertEqual("feature/task-9300", state.task_branch("task-9301"))


if __name__ == "__main__":
    unittest.main()

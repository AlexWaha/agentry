"""Commit-checkpoint diff delivery (task-0027: FR-53, FR-54, FR-55, NFR-4, C-9).

The four rows of contract C-9, each with kernel32 replaced by a mock so nothing
here ever types into a real console:

  1. Windows, CONIN$ opens, run parked on the commit  -> /diff queued
  2. Not Windows                                      -> capped diff preview
  3. Windows, CONIN$ fails                            -> capped diff preview
  4. Run not at a parking point                       -> nothing written, nothing said
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approvals
import diff_checkpoint
import git_state
import pretool_gate
import state
import tmproot

BUILD_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {"stages": [
        {"name": "implement", "owner": "dev"},
        {"name": "test", "owner": "qa-engineer"},
        {"name": "review", "owner": "reviewer"},
        {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
        {"name": "done", "owner": "orchestrator"},
    ]}},
}


def fake_kernel32(handle=0x1234, write_ok=True, written=None):
    """A stand-in for the kernel32 DLL. WriteConsoleInputW reports `written`
    records (all of them by default) through the pointer it is given."""
    k32 = unittest.mock.MagicMock()
    k32.CreateFileW.return_value = handle

    def write(_handle, records, count, pointer):
        pointer.contents.value = count if written is None else written
        return 1 if write_ok else 0

    k32.WriteConsoleInputW.side_effect = write
    return k32


class SandboxedRunTest(unittest.TestCase):
    """A real run.db in a project-local sandbox, pr mode, manual level."""

    def setUp(self):
        # tmproot forbids the console write for the whole suite; these tests mock
        # kernel32 instead, so they lift it for themselves only.
        env = unittest.mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(diff_checkpoint.NO_INJECT_ENV, None)
        tmp = tmproot.sandbox(self, "diff_checkpoint")
        for target, attr, value in (
                (state, "STATE_DIR", tmp), (state, "DB_PATH", tmp / "run.db"),
                (state, "load_pipeline", lambda: BUILD_PIPELINE),
                (pretool_gate, "workflow_mode", lambda: pretool_gate.WORKFLOW_PR),
                (approvals, "read", lambda *a, **k: approvals.MANUAL)):
            p = unittest.mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def seed(self, task, stage="ready", **fields):
        conn = state.connect()
        state.create_run(conn, task, "feature", stage)
        if fields:
            state.set_fields(conn, task, **fields)
        conn.close()

    def advance(self, task) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", task]), \
                redirect_stdout(buf):
            advance.main()
        return json.loads(buf.getvalue())


class WindowsInjectionTest(SandboxedRunTest):
    """C-9 row 1."""

    def test_parked_on_commit_queues_diff_and_enter_and_says_so(self):
        self.seed("task-0801", awaiting_human="commit")
        k32 = fake_kernel32()
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "changed_files",
                                           return_value="M a.py"), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff") as capped:
            note = diff_checkpoint.deliver("task-0801", inject=True)

        self.assertEqual(
            ("CONIN$", diff_checkpoint.GENERIC_READ | diff_checkpoint.GENERIC_WRITE,
             diff_checkpoint.FILE_SHARE_READ | diff_checkpoint.FILE_SHARE_WRITE, None,
             diff_checkpoint.OPEN_EXISTING, 0, None),
            k32.CreateFileW.call_args.args)
        self.assertEqual(1, k32.WriteConsoleInputW.call_count)
        # "/diff" and Enter, each as key-down and key-up
        self.assertEqual(12, k32.WriteConsoleInputW.call_args.args[2])
        k32.CloseHandle.assert_called_once()
        self.assertIn("/diff queued; if it has not rendered, run /diff", note)
        self.assertNotIn("above", note)  # the card keeps the turn open: nothing is promised
        self.assertIn("M a.py", note)  # the status travels with the success message
        capped.assert_not_called()  # NFR-6: the injection path computes no diff

    def test_records_are_slash_diff_then_enter_as_key_down_and_up(self):
        records = diff_checkpoint._records("/diff")
        chars = [(chr(r.event.key.char), r.event.key.key_down) for r in records]
        self.assertEqual(
            [("/", 1), ("/", 0), ("d", 1), ("d", 0), ("i", 1), ("i", 0),
             ("f", 1), ("f", 0), ("f", 1), ("f", 0), ("\r", 1), ("\r", 0)], chars)
        self.assertEqual(diff_checkpoint.VK_RETURN, records[10].event.key.virtual_key)
        self.assertEqual(20, diff_checkpoint.ctypes.sizeof(diff_checkpoint._InputRecord))

    def test_a_short_write_falls_back_to_the_preview(self):
        self.seed("task-0802", awaiting_human="commit")
        k32 = fake_kernel32(written=3)
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff", return_value="M x.py"):
            note = diff_checkpoint.deliver("task-0802", inject=True)

        self.assertNotIn("was queued", note)
        self.assertIn("M x.py", note)
        k32.CloseHandle.assert_called_once()


class PreviewFallbackTest(SandboxedRunTest):
    """C-9 rows 2 and 3, and the cap."""

    def test_non_windows_gives_the_preview_and_never_touches_kernel32(self):
        self.seed("task-0803", awaiting_human="commit")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=False), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32") as k32, \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M a.py\n+new line"):
            note = diff_checkpoint.deliver("task-0803", inject=True)

        k32.assert_not_called()
        self.assertIn("+new line", note)
        self.assertIn("preview field of the AskUserQuestion", note)
        self.assertIn("/diff", note)

    def test_conin_failure_gives_the_preview_and_no_error(self):
        self.seed("task-0804", awaiting_human="commit")
        k32 = fake_kernel32(handle=diff_checkpoint.INVALID_HANDLE)
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M a.py"):
            note = diff_checkpoint.deliver("task-0804", inject=True)

        k32.WriteConsoleInputW.assert_not_called()
        self.assertIn("M a.py", note)

    def test_a_kernel32_that_raises_still_gives_the_preview(self):
        self.seed("task-0805", awaiting_human="commit")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32",
                                           side_effect=OSError("no console")), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M a.py"):
            note = diff_checkpoint.deliver("task-0805", inject=True)

        self.assertIn("M a.py", note)

    def test_an_unreadable_git_degrades_to_the_bare_pointer(self):
        self.seed("task-0806", awaiting_human="commit")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=False), \
                unittest.mock.patch.object(git_state, "repos_for_task",
                                           side_effect=RuntimeError("git gone")):
            note = diff_checkpoint.deliver("task-0806", inject=True)

        self.assertEqual(" Tell the User to run /diff for the full diff.", note)

    def test_the_preview_is_capped_and_points_at_the_full_diff(self):
        huge = "\n".join(f"+line {i} " + "x" * 60 for i in range(500))
        with unittest.mock.patch.object(git_state, "repos_for_task",
                                        return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git",
                                           side_effect=[(0, "M a.py"), (0, huge)]):
            text = diff_checkpoint.capped_diff("task-0807")

        self.assertLessEqual(len(text), diff_checkpoint.PREVIEW_MAX_CHARS)
        self.assertTrue(text.startswith("M a.py\n+line 0"))
        self.assertTrue(text.endswith(diff_checkpoint.TRUNCATED))

    def test_a_small_diff_is_returned_whole_with_new_files_from_status(self):
        with unittest.mock.patch.object(git_state, "repos_for_task",
                                        return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git",
                                           side_effect=[(0, "?? new.py"), (0, "+a")]):
            text = diff_checkpoint.capped_diff("task-0808")

        self.assertEqual("?? new.py\n+a", text)

    def _assert_preview_after(self, task, k32):
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M a.py"):
            note = diff_checkpoint.deliver(task, inject=True)
        self.assertNotIn("was queued", note)
        self.assertIn("M a.py", note)

    def test_a_write_that_reports_failure_gives_the_preview_and_closes_the_handle(self):
        self.seed("task-0840", awaiting_human="commit")
        k32 = fake_kernel32(write_ok=False)
        self._assert_preview_after("task-0840", k32)
        k32.CloseHandle.assert_called_once()

    def test_a_write_that_raises_gives_the_preview_and_closes_the_handle(self):
        self.seed("task-0841", awaiting_human="commit")
        k32 = fake_kernel32()
        k32.WriteConsoleInputW.side_effect = OSError("console gone")
        self._assert_preview_after("task-0841", k32)
        k32.CloseHandle.assert_called_once()

    def test_a_close_that_raises_after_the_write_still_returns_text(self):
        self.seed("task-0842", awaiting_human="commit")
        k32 = fake_kernel32()
        k32.CloseHandle.side_effect = OSError("already closed")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M a.py"):
            note = diff_checkpoint.deliver("task-0842", inject=True)
        self.assertIn("/diff", note)

    def test_a_git_that_exits_nonzero_degrades_to_the_bare_pointer(self):
        self.seed("task-0843", awaiting_human="commit")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=False), \
                unittest.mock.patch.object(git_state, "repos_for_task",
                                           return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git", return_value=(1, "")):
            note = diff_checkpoint.deliver("task-0843", inject=True)

        self.assertEqual(" Tell the User to run /diff for the full diff.", note)

    def test_a_git_call_that_raises_degrades_to_the_bare_pointer(self):
        self.seed("task-0844", awaiting_human="commit")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=False), \
                unittest.mock.patch.object(git_state, "repos_for_task",
                                           return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git", side_effect=OSError("no git")):
            note = diff_checkpoint.deliver("task-0844", inject=True)

        self.assertEqual(" Tell the User to run /diff for the full diff.", note)


class ChangesTest(unittest.TestCase):
    """capped_diff / changed_files: repo headers, git timeouts, real git."""

    def test_git_is_called_with_a_short_timeout(self):
        with unittest.mock.patch.object(git_state, "repos_for_task",
                                        return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git",
                                           side_effect=[(0, "M a"), (0, "+b")]) as git:
            diff_checkpoint.capped_diff("task-0840")

        self.assertEqual(2, git.call_count)
        for call in git.call_args_list:
            self.assertEqual(5, call.kwargs["timeout"])

    def test_several_repos_are_headed_by_name_and_a_single_repo_is_not(self):
        two = [Path("backend"), Path("frontend")]
        with unittest.mock.patch.object(git_state, "repos_for_task", return_value=two), \
                unittest.mock.patch.object(git_state, "_git",
                                           side_effect=[(0, "M b.py"), (0, "+b"),
                                                        (0, "M f.ts"), (0, "+f")]):
            text = diff_checkpoint.capped_diff("task-0841")
        self.assertEqual("# backend\nM b.py\n+b\n# frontend\nM f.ts\n+f", text)

        with unittest.mock.patch.object(git_state, "repos_for_task", return_value=two[:1]), \
                unittest.mock.patch.object(git_state, "_git",
                                           side_effect=[(0, "M b.py"), (0, "+b")]):
            self.assertEqual("M b.py\n+b", diff_checkpoint.capped_diff("task-0841"))

    def test_changed_files_asks_for_the_status_only(self):
        with unittest.mock.patch.object(git_state, "repos_for_task",
                                        return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git",
                                           return_value=(0, "?? new.py")) as git:
            self.assertEqual("?? new.py", diff_checkpoint.changed_files("task-0842"))
        git.assert_called_once()

    def test_real_git_in_a_tmp_repo_reports_modified_and_new_files(self):
        import subprocess
        repo = tmproot.sandbox(self, "diff_real_git")

        def git(*args):
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                            "-c", "user.email=t@example.com", *args],
                           check=True, capture_output=True)

        git("init", "-q")
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        git("add", "a.txt")
        git("commit", "-q", "-m", "init")
        (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        (repo / "new.txt").write_text("fresh\n", encoding="utf-8")

        with unittest.mock.patch.object(git_state, "repos_for_task", return_value=[repo]):
            diff = diff_checkpoint.capped_diff("task-0843")
            files = diff_checkpoint.changed_files("task-0843")

        self.assertIn("a.txt", files)
        self.assertIn("?? new.txt", files)
        self.assertIn("+two", diff)
        self.assertNotIn("+two", files)


class NotAParkingPointTest(SandboxedRunTest):
    """C-9 row 4 and FR-55: no write to the console input buffer, ever."""

    def _assert_silent(self, task):
        k32 = fake_kernel32()
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32):
            note = diff_checkpoint.deliver(task, inject=True)
            injected = diff_checkpoint.inject_diff(task)
        self.assertEqual("", note)
        self.assertFalse(injected)
        k32.CreateFileW.assert_not_called()
        k32.WriteConsoleInputW.assert_not_called()

    def test_mid_stage_runs_never_write(self):
        for i, stage in enumerate(("implement", "test", "review")):
            with self.subTest(stage=stage):
                self.seed(f"task-081{i}", stage=stage)
                self._assert_silent(f"task-081{i}")

    def test_a_ready_run_that_is_not_yet_parked_never_writes(self):
        self.seed("task-0814", stage="ready")
        self._assert_silent("task-0814")

    def test_an_already_approved_commit_is_not_a_parking_point(self):
        self.seed("task-0815", awaiting_human="commit", commit_approved=1)
        self._assert_silent("task-0815")

    def test_a_push_or_blocked_park_never_writes(self):
        for i, waiting in enumerate(("push", "merge", "plan", "blocked")):
            with self.subTest(waiting=waiting):
                self.seed(f"task-082{i}", awaiting_human=waiting)
                self._assert_silent(f"task-082{i}")

    def test_the_suite_wide_switch_forbids_the_write_even_at_a_park(self):
        self.seed("task-0817", awaiting_human="commit")
        k32 = fake_kernel32()
        os.environ[diff_checkpoint.NO_INJECT_ENV] = "1"
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff", return_value="M a"):
            note = diff_checkpoint.deliver("task-0817", inject=True)

        k32.CreateFileW.assert_not_called()
        self.assertIn("M a", note)  # the preview still reaches the checkpoint

    def test_an_unknown_task_never_writes(self):
        self._assert_silent("task-0899")

    def test_an_unreadable_run_store_never_writes_and_never_raises(self):
        self.seed("task-0818", awaiting_human="commit")
        k32 = fake_kernel32()
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(state, "connect", side_effect=OSError("locked")):
            note = diff_checkpoint.deliver("task-0818", inject=True)

        self.assertEqual("", note)
        k32.CreateFileW.assert_not_called()

    def test_a_caller_cannot_force_the_injection_outside_a_park(self):
        """The check lives in inject_diff itself: calling it directly, the way a
        careless future caller would, still writes nothing."""
        self.seed("task-0816", stage="implement")
        with unittest.mock.patch.object(diff_checkpoint, "_queue_keystrokes") as queue:
            self.assertFalse(diff_checkpoint.inject_diff("task-0816"))
        queue.assert_not_called()


class AdvanceParkIntegrationTest(SandboxedRunTest):
    """The single call site: advance.py parking at the ready stage."""

    def test_a_fresh_commit_park_injects_once_and_reports_it(self):
        self.seed("task-0830")
        k32 = fake_kernel32()
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff", return_value=""):
            first = self.advance("task-0830")
            second = self.advance("task-0830")

        self.assertEqual("park", first["action"])
        self.assertIn("/diff queued; if it has not rendered", first["message"])
        self.assertEqual("park", second["action"])
        self.assertEqual(1, k32.WriteConsoleInputW.call_count)  # no second keystroke burst

    def test_a_non_windows_park_carries_the_preview_in_its_message(self):
        self.seed("task-0831")
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=False), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="M feature.py"):
            out = self.advance("task-0831")

        self.assertEqual("park", out["action"])
        self.assertIn("M feature.py", out["message"])
        self.assertIn("wait for User commit approval", out["message"])

    def test_an_auto_approved_commit_never_writes(self):
        self.seed("task-0832")
        k32 = fake_kernel32()
        with unittest.mock.patch.object(approvals, "read", return_value=approvals.AUTO), \
                unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_kernel32", return_value=k32):
            out = self.advance("task-0832")

        self.assertIn("auto-approved", out["message"])
        k32.CreateFileW.assert_not_called()
        k32.WriteConsoleInputW.assert_not_called()


class MidStageDriveTest(SandboxedRunTest):
    """FR-55 acceptance: drive a run through implement, test and review with the
    injection call site instrumented. Nothing is queued until the commit park."""

    def test_no_keystrokes_are_queued_before_the_commit_park(self):
        self.seed("task-0850", stage="implement")
        steps = []
        with unittest.mock.patch.object(diff_checkpoint, "_is_windows", return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "_queue_keystrokes",
                                           return_value=True) as queue, \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff", return_value=""):
            for _ in range(6):
                out = self.advance("task-0850")
                steps.append((out["action"], queue.call_count))
                if out["action"] == "park":
                    break

        self.assertEqual(
            [("advanced", 0), ("advanced", 0), ("advanced", 0), ("park", 1)], steps)


class SingleWriterTest(unittest.TestCase):
    """FR-55 by construction: the console write API has one home and one door."""

    TOOLS = Path(__file__).resolve().parents[1]
    FORBIDDEN_ELSEWHERE = ("WriteConsoleInputW", "CONIN$", "_queue_keystrokes", "inject_diff")

    def _tool_sources(self):
        return [p for p in self.TOOLS.rglob("*.py")
                if "tests" not in p.relative_to(self.TOOLS).parts
                and p.name != "diff_checkpoint.py"]

    def test_no_other_tool_names_the_console_write_api(self):
        self.assertTrue(self._tool_sources())
        for path in self._tool_sources():
            text = path.read_text(encoding="utf-8")
            for name in self.FORBIDDEN_ELSEWHERE:
                with self.subTest(file=path.name, name=name):
                    self.assertNotIn(name, text)

    def _callers_of(self, callee):
        tree = ast.parse(Path(diff_checkpoint.__file__).read_text(encoding="utf-8"))
        callers = []
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == callee:
                        callers.append(func.name)
        return callers

    def test_the_keystroke_writer_is_called_only_from_inject_diff(self):
        self.assertEqual(["inject_diff"], self._callers_of("_queue_keystrokes"))

    def test_inject_diff_is_called_only_from_deliver(self):
        self.assertEqual(["deliver"], self._callers_of("inject_diff"))


class SuiteWideGuardTest(unittest.TestCase):
    """The AGENTRY_NO_CONSOLE_INJECT guard must reach every test that can drive
    advance.py to the commit park."""

    TESTS = Path(__file__).resolve().parent
    REACHES_ADVANCE = re.compile(r"^(import advance|from advance import)", re.MULTILINE)

    def test_tmproot_sets_the_switch_for_the_whole_suite(self):
        self.assertTrue(os.environ.get(diff_checkpoint.NO_INJECT_ENV))

    def test_every_module_that_imports_advance_imports_tmproot(self):
        reaching = []
        for path in sorted(self.TESTS.glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if self.REACHES_ADVANCE.search(text):
                reaching.append(path.name)
                with self.subTest(file=path.name):
                    self.assertRegex(text, r"(?m)^import tmproot$")
        self.assertIn("test_diff_checkpoint.py", reaching)
        self.assertGreater(len(reaching), 1)


if __name__ == "__main__":
    unittest.main()

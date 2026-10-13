"""task-0124 part B: every gate is bounded in time and in what it will resolve.

"A hook that times out is an allow", so each of these closes a way to make a gate
run long, or to make it open a network connection, instead of answering:

- the push path shares ONE deadline and running out of it denies;
- glob expansion is lazy and capped; UNC paths are never resolved;
- the exit gate runs its command in its own process group with output in a file,
  so a grandchild holding the output handle cannot block it, and a timeout kills
  the whole tree;
- the leak guard reads the trunk and the remote from pipeline.json;
- an approve.py reason is bounded and carries no control characters.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import itertools
import os
import subprocess
import sys
import time
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
import leak_guard
import pretool_gate
import state
import test_event_writers as ew
import tmproot

UNC_SPELLINGS = (
    r"\\server\share\file.txt",
    "//server/share/file.txt",
    r"\\?\UNC\server\share\file.txt",
    "//?/UNC/server/share/file.txt",
    r"\\localhost\E$\Personal\Agenty\.claude\tools\x.py",
)
LOCAL_SPELLINGS = (r"C:\Users\x\file.txt", "/tmp/x", "relative/path", "//", "/", "")


def git(args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                             text=True, timeout=20).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, 9)


def wait_gone(pid: int, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not process_alive(pid):
            return True
        time.sleep(0.2)
    return not process_alive(pid)


class FakeClock:
    """time.monotonic stand-in that only moves when a fake git call 'runs'."""

    def __init__(self, step: float = 6.0):
        self.now = 1000.0
        self.step = step
        self.timeouts = []

    def monotonic(self) -> float:
        return self.now

    def run(self, argv, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        self.now += self.step  # every git call 'takes' this long
        return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")


class PushDeadlineTest(unittest.TestCase):
    def tearDown(self):
        pretool_gate.end_push_budget()

    def test_every_git_call_gets_the_budget_that_is_left_and_the_budget_runs_out(self):
        step = pretool_gate.PUSH_BUDGET / 3
        clock = FakeClock(step)
        with unittest.mock.patch.object(pretool_gate.time, "monotonic", clock.monotonic), \
                unittest.mock.patch.object(pretool_gate.subprocess, "run", clock.run):
            pretool_gate.start_push_budget()
            pretool_gate.git_run(["git", "a"], ".")  # the whole budget left, capped at 10
            pretool_gate.git_run(["git", "b"], ".")  # two thirds left
            pretool_gate.git_run(["git", "c"], ".")  # one third left
            with self.assertRaises(pretool_gate.PushBudgetExpired):
                pretool_gate.git_run(["git", "d"], ".")  # nothing left: not even started
        self.assertEqual([pretool_gate.GIT_CALL_TIMEOUT, pretool_gate.PUSH_BUDGET - step,
                          pretool_gate.PUSH_BUDGET - 2 * step], clock.timeouts)

    def test_a_git_call_that_times_out_inside_the_budget_is_an_expiry_not_a_skip(self):
        pretool_gate.start_push_budget()
        boom = subprocess.TimeoutExpired(cmd="git", timeout=1)
        with unittest.mock.patch.object(pretool_gate.subprocess, "run", side_effect=boom):
            with self.assertRaises(pretool_gate.PushBudgetExpired):
                pretool_gate.current_branch("", str(state.ROOT))

    def test_without_a_budget_a_timeout_stays_the_plain_timeout_the_callers_handle(self):
        boom = subprocess.TimeoutExpired(cmd="git", timeout=1)
        with unittest.mock.patch.object(pretool_gate.subprocess, "run", side_effect=boom):
            self.assertEqual("", pretool_gate.current_branch("", str(state.ROOT)))

    def test_the_leak_guard_is_handed_the_same_deadline(self):
        pretool_gate.start_push_budget()
        deadline = pretool_gate._push_deadline
        with unittest.mock.patch.object(leak_guard, "check", return_value=(0, [])) as check:
            pretool_gate.leak_check("HEAD", ".")
        self.assertEqual(deadline, check.call_args.kwargs["deadline"])

    def test_an_expired_budget_inside_the_leak_check_is_a_leak(self):
        with unittest.mock.patch.object(pretool_gate, "push_repo", return_value="."), \
                unittest.mock.patch.object(pretool_gate, "leak_check",
                                           side_effect=pretool_gate.PushBudgetExpired()):
            reason = pretool_gate.push_leak_reason("git push origin x", ".", ["x"])
        self.assertIn("time budget", reason)

    def test_the_leak_guard_refuses_to_start_a_git_call_past_the_deadline(self):
        code, lines = leak_guard.check("HEAD", ".", deadline=time.monotonic() - 1)
        self.assertEqual(2, code)
        self.assertIn("time budget", " ".join(lines))

    def test_handle_bash_denies_a_push_whose_checks_run_out_of_time(self):
        with unittest.mock.patch.object(pretool_gate, "current_branch",
                                        side_effect=pretool_gate.PushBudgetExpired()):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = pretool_gate.handle_bash("git push -u origin bugfix/task-9141",
                                                cwd=str(state.ROOT))
        self.assertEqual(2, code)
        self.assertIn("time budget", err.getvalue())
        self.assertIsNone(pretool_gate._push_deadline)

    def test_a_push_and_a_commit_both_start_the_budget_and_a_non_git_command_does_not(self):
        seen = []

        def spy(command="", cwd=""):
            seen.append(pretool_gate._push_deadline)
            raise pretool_gate.PushBudgetExpired()

        with unittest.mock.patch.object(pretool_gate, "current_branch", spy), \
                contextlib.redirect_stderr(io.StringIO()):
            pretool_gate.handle_bash("git push origin bugfix/task-9141", cwd=str(state.ROOT))
            pretool_gate.handle_bash("git commit -m x", cwd=str(state.ROOT))
        self.assertEqual(2, len(seen))
        self.assertIsNotNone(seen[0])
        self.assertIsNotNone(seen[1])
        self.assertIsNone(pretool_gate._push_deadline)
        with unittest.mock.patch.object(pretool_gate, "start_push_budget") as armed:
            pretool_gate.handle_bash("ls -la", cwd=str(state.ROOT))
        armed.assert_not_called()


class UncAndGlobTest(unittest.TestCase):
    def test_is_unc_recognises_every_network_spelling_and_no_local_one(self):
        for path in UNC_SPELLINGS:
            self.assertTrue(pretool_gate.is_unc(path), path)
        for path in LOCAL_SPELLINGS:
            self.assertFalse(pretool_gate.is_unc(path), path)

    def test_path_forms_never_resolves_a_unc_path(self):
        with unittest.mock.patch.object(pretool_gate.os.path, "realpath",
                                        side_effect=AssertionError("realpath on a UNC path")):
            for path in UNC_SPELLINGS:
                forms = pretool_gate.path_forms(path)
                self.assertEqual(1, len(forms), path)
                self.assertTrue(pretool_gate.is_unc(forms[0]), path)

    def test_a_relative_path_under_a_unc_cwd_is_not_resolved_either(self):
        with unittest.mock.patch.object(pretool_gate.os.path, "realpath",
                                        side_effect=AssertionError("realpath on a UNC path")):
            forms = pretool_gate.path_forms("sub/file.txt", r"\\server\share")
        self.assertEqual(["sub/file.txt"], forms)

    def test_a_unc_path_is_protected_for_every_subagent_write(self):
        for path in UNC_SPELLINGS:
            self.assertTrue(pretool_gate.is_live_harness(path), path)
            self.assertTrue(agent_gate.is_protected(path), path)

    def test_a_unc_binary_or_script_is_never_trusted(self):
        for path in UNC_SPELLINGS:
            self.assertTrue(agent_gate.planted_binary(path, str(state.ROOT)), path)

    def test_a_unc_path_is_outside_every_zone(self):
        denial = agent_gate.zone_path_denial(UNC_SPELLINGS[0], [str(state.ROOT)], "z1", {})
        self.assertIn("network path", denial)

    def test_the_orchestrator_may_not_write_to_a_unc_path(self):
        for path in UNC_SPELLINGS:
            self.assertFalse(pretool_gate.orch_allowed_path(path), path)

    def test_glob_hits_takes_lazily_and_stops_at_the_cap(self):
        pulled = []

        def endless(_pattern):
            for i in itertools.count():
                pulled.append(i)
                yield f"hit-{i}"

        with unittest.mock.patch.object(agent_gate.glob, "iglob", endless):
            hits = agent_gate.glob_hits("*")
        self.assertEqual(agent_gate.MAX_GLOB_HITS, len(hits))
        self.assertEqual(agent_gate.MAX_GLOB_HITS, len(pulled))

    def test_glob_hits_never_expands_a_unc_pattern(self):
        with unittest.mock.patch.object(agent_gate.glob, "iglob",
                                        side_effect=AssertionError("globbed a UNC path")):
            self.assertEqual([], agent_gate.glob_hits(r"\\server\share\*"))

    def test_the_gates_no_longer_call_glob_glob(self):
        for name in ("agent_gate.py", "pretool_gate.py"):
            text = (PIPELINE_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("glob.glob(", text, name)


class GateProcessTreeTest(unittest.TestCase):
    """gate.run_command with a grandchild that holds the output handle."""

    HOLDER = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    LEAVER = (
        "import subprocess, sys\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(25)'])\n"
        "open(sys.argv[1], 'w').write(str(g.pid))\n"
        "print('parent-done')\n"
    )

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "gate_bounds_")

    def spawn_script(self, name: str, text: str) -> tuple:
        script = self.tmp / name
        script.write_text(text, encoding="utf-8")
        pidfile = self.tmp / f"{name}.pid"
        return f'"{sys.executable}" "{script}" "{pidfile}"', pidfile

    def grandchild(self, pidfile: Path) -> int:
        return int(pidfile.read_text(encoding="utf-8"))

    def test_a_timeout_returns_at_the_timeout_and_kills_the_grandchild_too(self):
        cmd, pidfile = self.spawn_script("holder.py", self.HOLDER)
        started = time.monotonic()
        code, output = gate.run_command(cmd, str(self.tmp), 3)
        elapsed = time.monotonic() - started
        self.assertEqual(124, code)
        self.assertIn("timed out after 3s", output)
        self.assertLess(elapsed, 30)
        pid = self.grandchild(pidfile)
        self.addCleanup(kill_pid, pid)
        self.assertTrue(wait_gone(pid), "the grandchild survived the timeout")

    def test_a_grandchild_that_outlives_the_command_does_not_block_the_gate(self):
        cmd, pidfile = self.spawn_script("leaver.py", self.LEAVER)
        started = time.monotonic()
        code, output = gate.run_command(cmd, str(self.tmp), 60)
        elapsed = time.monotonic() - started
        pid = self.grandchild(pidfile)
        self.addCleanup(self.drop_log_files)
        self.addCleanup(wait_gone, pid)
        self.addCleanup(kill_pid, pid)
        self.assertEqual(0, code)
        self.assertIn("parent-done", output)
        self.assertLess(elapsed, 15, "the gate waited for a grandchild holding the handle")

    def drop_log_files(self):
        for leftover in tmproot.RUN_ROOT.glob("gaterun-*.log"):
            with contextlib.suppress(OSError):
                leftover.unlink()

    def test_run_gate_reports_a_timeout_as_exit_124_and_a_normal_exit_unchanged(self):
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": f'"{sys.executable}" -c "import time; time.sleep(60)"'}},
            {"name": "implement", "exit_gate": {"cmd": f'"{sys.executable}" -c "raise SystemExit(3)"'}},
        ]}}}
        with unittest.mock.patch.object(gate, "GATE_TIMEOUT", 2):
            slow = gate.run_gate(pipeline, "test")
            failed = gate.run_gate(pipeline, "implement")
        self.assertEqual((124, False), (slow["exit"], slow["passed"]))
        self.assertIn("timed out", slow["output"])
        self.assertEqual((3, False), (failed["exit"], failed["passed"]))

    def test_the_output_is_a_tail_not_the_whole_stream(self):
        cmd = f'"{sys.executable}" -c "print(\'x\' * 20000)"'
        code, output = gate.run_command(cmd, str(self.tmp), 30)
        self.assertEqual(0, code)
        self.assertLessEqual(len(output), gate.OUTPUT_TAIL_BYTES)

    def test_no_pipe_is_used_and_the_command_gets_its_own_group(self):
        with unittest.mock.patch.object(gate.subprocess, "Popen",
                                        wraps=gate.subprocess.Popen) as popen:
            gate.run_command(f'"{sys.executable}" -c "pass"', str(self.tmp), 30)
        kwargs = popen.call_args.kwargs
        self.assertNotEqual(subprocess.PIPE, kwargs["stdout"])
        self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"))


class LeakGuardConfigTest(unittest.TestCase):
    def setUp(self):
        self.repo = tmproot.sandbox(self, "leak_cfg_")
        git(["init", "-q", "-b", "trunk"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        (self.repo / "README.md").write_text("x", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["update-ref", "refs/remotes/upstream/trunk", "HEAD"], self.repo)
        leak = self.repo / ".agentry" / "memory" / "lessons.md"
        leak.parent.mkdir(parents=True)
        leak.write_text("secret", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "leak"], self.repo)

    def configured(self, **config):
        return unittest.mock.patch.object(state, "load_pipeline", return_value=config)

    def test_the_trunk_and_remote_come_from_the_config(self):
        with self.configured(main_branch="trunk", remote="upstream"):
            self.assertEqual("upstream/trunk", leak_guard.base_ref())
            code, lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual((1, [".agentry/memory/lessons.md"]), (code, lines))

    def test_without_the_keys_it_compares_against_origin_main(self):
        with self.configured():
            self.assertEqual("origin/main", leak_guard.base_ref())
            code, lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual(2, code)
        self.assertIn("origin/main", lines[0])

    def test_an_unresolved_placeholder_reads_as_the_default(self):
        with self.configured(main_branch="{{MAIN_BRANCH}}", remote="  "):
            self.assertEqual("origin/main", leak_guard.base_ref())


    def test_approve_and_the_leak_guard_read_the_trunk_through_one_reader(self):
        for config in ({}, {"main_branch": "trunk"}, {"main_branch": "{{MAIN_BRANCH}}"}):
            with self.subTest(config=config), self.configured(**config):
                self.assertEqual(leak_guard.trunk_name(), approve.trunk_name())
                self.assertTrue(leak_guard.base_ref().endswith("/" + approve.trunk_name()))


class ReasonTest(ew.Harness):
    def test_clean_reason_caps_the_length(self):
        self.assertEqual(500, len(approve.clean_reason("a" * 5000)))

    def test_clean_reason_removes_control_and_format_characters(self):
        dirty = "line one\nline two\x1b[31m red\x00 nul\u202e bidi\r\ttabbed"
        cleaned = approve.clean_reason(dirty)
        self.assertEqual("line one line two [31m red  nul  bidi  tabbed", cleaned)
        self.assertFalse(any(ord(ch) < 32 or ord(ch) == 127 or ch == "\u202e" for ch in cleaned))

    def test_a_reason_of_only_control_characters_is_empty(self):
        self.assertEqual("", approve.clean_reason("\n\x1b\u202e \t"))
        self.assertEqual("", approve.clean_reason(None))

    def test_a_rejection_stores_and_journals_the_cleaned_bounded_reason(self):
        self.seed(stage="ready", commit_approved=0)
        dirty = ("diff touches an unrelated file\n\x1b]0;title\x07 " + "x" * 2000)
        code, out = self.approve("--reject", "--reason", dirty)
        self.assertEqual(0, code)
        rejected = next(r for r in self.journal() if r["kind"] == "rejected")
        stored = rejected["detail"]["reason"]
        self.assertTrue(stored)
        self.assertLessEqual(len(stored), approve.REASON_MAX)
        self.assertNotIn("\n", stored)
        self.assertNotIn("\x1b", stored)
        self.assertIn("rejected", out["message"])

    def test_a_reject_reason_of_only_control_characters_is_refused(self):
        self.seed(stage="ready")
        code, out = self.approve("--reject", "--reason", "\n\x1b")
        self.assertEqual(2, code)
        self.assertIn("--reason", out["error"])


if __name__ == "__main__":
    unittest.main()

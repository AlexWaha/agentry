"""task-0124 security fix round, part F2: the pretool gate, leak guard, exit gate and
reason cleaner.

- a UNC path is never statted or opened by the push / commit / merge path, and a
  git command carrying one is refused up front;
- the push time budget starts before any repo work, and a watchdog thread denies a
  call the main thread cannot finish in time;
- three or more leading separators are a UNC path too;
- the leak guard builds its base from refs/remotes/ and refuses odd remote / trunk names;
- the exit gate calls taskkill by absolute path and only splices a plain task id;
- approve.clean_reason also removes line / paragraph separators, private use,
  unassigned and surrogate characters.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
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

import approve
import gate
import leak_guard
import pretool_gate
import state
import tmproot

UNC_DIR = "//srv-f2/share/repo"


def git(args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


class UncSpellingTest(unittest.TestCase):
    def test_two_three_and_more_leading_separators_are_unc(self):
        for path in ("//h/s", r"\\h\s", "///h/s", "////h/s", r"\\\h\s", "/\\/h/s", "///?/UNC/h/s"):
            self.assertTrue(pretool_gate.is_unc(path), path)

    def test_a_bare_root_or_run_of_separators_is_not_unc(self):
        for path in ("/", "//", "///", "/x", "x//y", "C:/x", ""):
            self.assertFalse(pretool_gate.is_unc(path), path)

    def test_path_forms_never_resolves_a_three_slash_share(self):
        with unittest.mock.patch.object(pretool_gate.os.path, "realpath",
                                        side_effect=AssertionError("realpath on a UNC path")):
            self.assertEqual(["///h/s/x"], pretool_gate.path_forms("///h/s/x"))


class UncOperandTest(unittest.TestCase):
    def test_every_spelling_of_a_network_directory_or_file_is_found(self):
        for command in (
            "git -C //h/s/repo push origin bugfix/task-1",
            "git -c a=b -C //h/s/repo push origin bugfix/task-1",
            "git -C '\\\\h\\s\\repo' commit -m x",
            "git -C///h/s/repo commit -m x",
            "cd //h/s/repo && git push origin bugfix/task-1",
            "git --git-dir=//h/s/repo/.git push origin x",
            "git --work-tree //h/s/repo commit -m x",
            "git commit -F //h/s/msg.txt",
            "git commit -F//h/s/msg.txt",
            "git commit -aF //h/s/msg.txt",
            "git commit --file=//h/s/msg.txt",
            "git commit --file //h/s/msg.txt",
            "git commit -F '//h/s/msg.txt'",
            "git push //h/s/repo.git bugfix/task-1",
            "git push ///h/s/repo.git bugfix/task-1",
            "git merge //h/s/repo",
            "git pull //h/s/repo main",
            "bash -c 'git -C //h/s/repo push origin x'",
        ):
            self.assertIsNotNone(pretool_gate.unc_operand(command, ""), command)

    def test_a_unc_working_directory_is_found(self):
        self.assertEqual(UNC_DIR, pretool_gate.unc_operand("git commit -m x", UNC_DIR))

    def test_local_commands_and_messages_that_look_like_paths_are_not(self):
        for command in (
            "git push origin bugfix/task-1",
            "git -C ../repo push origin bugfix/task-1",
            "git commit -m '// fix the thing'",
            "git commit -am '//x'",
            "git commit -m //x -F msg.txt",
            "git commit --message '//x'",
            "git commit --message=//x",
            "git commit -mfix//x",
            "git merge -m '//x' feature/task-1",
            "git commit -m x && git push origin bugfix/task-1",
            "git merge-base --is-ancestor origin/main HEAD",
        ):
            self.assertIsNone(pretool_gate.unc_operand(command, str(state.ROOT)), command)


class UncStatTest(unittest.TestCase):
    """Nothing on the push / commit / merge path touches the network."""

    def setUp(self):
        self.seen = []
        real = os.stat

        def spy(path, *args, **kwargs):
            text = os.fsdecode(path) if isinstance(path, bytes) else str(path)
            if pretool_gate.is_unc(text):
                self.seen.append(text)
            return real(path, *args, **kwargs)

        patcher = unittest.mock.patch.object(os, "stat", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def handle(self, command: str, cwd: str | None = None) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                unittest.mock.patch.object(pretool_gate.subprocess, "run",
                                           side_effect=AssertionError("git was started")):
            code = pretool_gate.handle_bash(command, cwd=str(state.ROOT) if cwd is None else cwd)
        return code, err.getvalue()

    def test_repo_candidates_drop_a_unc_directory_and_keep_the_local_ones(self):
        for command in (f"git -C {UNC_DIR} push origin x", f"cd {UNC_DIR} && git push origin x"):
            self.assertEqual([state.ROOT], pretool_gate.repo_candidates(command, ""))
        self.assertEqual([state.ROOT], pretool_gate.repo_candidates("git push", UNC_DIR))

    def test_every_repo_helper_skips_a_unc_candidate_without_a_stat(self):
        command = f"git -C {UNC_DIR} push origin x"
        with unittest.mock.patch.object(pretool_gate.subprocess, "run",
                                        side_effect=subprocess.SubprocessError("no git")):
            self.assertEqual("", pretool_gate.current_branch(command, ""))
            self.assertIsNone(pretool_gate.local_branches(command, ""))
            self.assertIsNone(pretool_gate.push_repo(command, ""))
            self.assertFalse(pretool_gate.repo_bootstrap(command, "", "main"))
            self.assertEqual([], pretool_gate.staged_files(command, ""))
        self.assertEqual([], self.seen)

    def test_a_unc_message_file_is_never_read(self):
        self.assertEqual("", pretool_gate.read_message_file("//srv-f2/share/msg.txt", ""))
        self.assertEqual("", pretool_gate.read_message_file("msg.txt", UNC_DIR))
        self.assertEqual([], self.seen)

    def test_a_push_or_commit_naming_a_network_path_is_refused_before_any_git_call(self):
        for command in (
            f"git -C {UNC_DIR} push origin bugfix/task-9",
            f"cd {UNC_DIR} && git commit -m x",
            "git commit -F //srv-f2/share/msg.txt",
            "git merge //srv-f2/share/repo",
        ):
            code, err = self.handle(command)
            self.assertEqual(2, code, command)
            self.assertIn("network path", err, command)
        self.assertEqual([], self.seen)

    def test_a_unc_working_directory_refuses_a_commit(self):
        code, err = self.handle("git commit -m x", cwd=UNC_DIR)
        self.assertEqual(2, code)
        self.assertIn("network path", err)
        self.assertEqual([], self.seen)

    def test_a_command_that_is_not_a_commit_push_or_merge_is_left_alone(self):
        code, _err = self.handle(f"git -C {UNC_DIR} status")
        self.assertEqual(0, code)


class FakeClock:
    """time.monotonic stand-in that only moves when a fake git call 'runs'."""

    def __init__(self):
        self.now = 1000.0
        self.deadline_seen = []

    def monotonic(self) -> float:
        return self.now

    def run(self, argv, **kwargs):
        self.deadline_seen.append(pretool_gate._push_deadline is not None)
        self.now += 6.0
        return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")


class BudgetStartsFirstTest(unittest.TestCase):
    def tearDown(self):
        pretool_gate.end_push_budget()

    def run_command(self, command: str) -> tuple:
        clock = FakeClock()
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate.time, "monotonic", clock.monotonic), \
                unittest.mock.patch.object(pretool_gate.subprocess, "run", clock.run), \
                contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(command, cwd=str(state.ROOT))
        return code, err.getvalue(), clock

    def test_the_commit_bootstrap_check_of_a_chained_push_runs_under_the_budget(self):
        code, err, clock = self.run_command("git commit -m x && git push origin bugfix/task-9")
        self.assertTrue(clock.deadline_seen)
        self.assertTrue(all(clock.deadline_seen), clock.deadline_seen)
        self.assertEqual(2, code)
        self.assertIn("time budget", err)

    def test_the_branch_base_check_of_a_chained_push_runs_under_the_budget(self):
        _code, _err, clock = self.run_command(
            "git checkout -b bugfix/task-9 && git push origin bugfix/task-9")
        self.assertTrue(clock.deadline_seen)
        self.assertTrue(all(clock.deadline_seen), clock.deadline_seen)

    def test_the_budget_is_over_when_the_command_has_been_judged(self):
        self.run_command("git push origin bugfix/task-9")
        self.assertIsNone(pretool_gate._push_deadline)

    def test_a_plain_commit_runs_under_the_budget_too(self):
        _code, _err, clock = self.run_command("git commit -m x")
        self.assertTrue(clock.deadline_seen)
        self.assertTrue(all(clock.deadline_seen), clock.deadline_seen)

    def test_the_staged_files_read_draws_on_the_budget(self):
        pretool_gate.start_push_budget(-1.0)
        with self.assertRaises(pretool_gate.PushBudgetExpired):
            pretool_gate.staged_files("git commit -m x", str(state.ROOT))

    def test_a_push_spelled_so_no_word_list_sees_it_still_arms_the_budget(self):
        for command in ('git pu""sh origin bugfix/task-9', "git pu\\sh origin x",
                        "git -c alias.p=push p origin x"):
            with self.subTest(command=command), \
                    unittest.mock.patch.object(pretool_gate, "start_push_budget",
                                               wraps=pretool_gate.start_push_budget) as armed:
                self.run_command(command)
            armed.assert_called_once_with()

    def test_a_command_that_does_not_mention_git_runs_without_a_budget(self):
        with unittest.mock.patch.object(pretool_gate, "start_push_budget") as armed:
            code, _err, _clock = self.run_command("ls -la")
        self.assertEqual(0, code)
        armed.assert_not_called()


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(pretool_gate, "WATCHDOG_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.exit = unittest.mock.patch.object(pretool_gate.os, "_exit").start()
        self.addCleanup(unittest.mock.patch.stopall)

    def evaluate(self, payload: dict, handler_seconds: float) -> tuple:
        def slow(*_args, **_kwargs):
            time.sleep(handler_seconds)
            return 0

        # The timer writes with os.write on fds 1 and 2 (no stream locks), so capture those.
        written = {1: b"", 2: b""}

        def capture(fd, data):
            written[fd] += data
            return len(data)

        with unittest.mock.patch.object(pretool_gate, "read_payload", return_value=payload), \
                unittest.mock.patch.object(pretool_gate, "handle_bash", slow), \
                unittest.mock.patch.object(pretool_gate, "handle_edit", slow), \
                unittest.mock.patch.object(pretool_gate.os, "write", capture):
            pretool_gate.evaluate()
            time.sleep(0.4)  # past the shrunk deadline: a timer still alive would fire now
        return written[2].decode("utf-8"), written[1].decode("utf-8")

    def test_a_slow_subagent_call_is_denied_by_the_timer(self):
        for payload in ({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_id": "a1"},
                        {"tool_name": "Write", "tool_input": {"file_path": "x.py"}, "agent_id": "a1"}):
            self.exit.reset_mock()
            err, out = self.evaluate(payload, 0.8)
            self.exit.assert_called_once_with(2)
            self.assertIn("did not finish", err)
            decision = json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]
            self.assertEqual("deny", decision["permissionDecision"])

    def test_a_slow_orchestrator_command_that_mentions_git_is_denied_by_the_timer(self):
        for command in ("git push origin bugfix/task-1", "git commit -m x", "git merge x",
                        "git pull . x", "git status", 'git pu""sh origin x'):
            self.exit.reset_mock()
            self.evaluate({"tool_name": "Bash", "tool_input": {"command": command}}, 0.8)
            self.exit.assert_called_once_with(2)

    def test_a_slow_orchestrator_command_that_does_not_mention_git_keeps_the_old_behaviour(self):
        for payload in ({"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
                        {"tool_name": "Write", "tool_input": {"file_path": "docs/x.md"}}):
            self.exit.reset_mock()
            self.evaluate(payload, 0.8)
            self.exit.assert_not_called()

    def test_a_timer_that_cannot_start_denies_instead_of_running_unguarded(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_id": "a1"}
        with unittest.mock.patch.object(pretool_gate.threading.Timer, "start",
                                        side_effect=RuntimeError("can't start new thread")):
            err, out = self.evaluate(payload, 0.0)
        self.exit.assert_called_with(2)
        self.assertIn("did not finish", err)
        self.assertEqual("deny", json.loads(out.strip().splitlines()[-1])[
            "hookSpecificOutput"]["permissionDecision"])

    def test_the_shared_helper_writes_with_os_write_and_exits_2(self):
        written = []
        with unittest.mock.patch.object(pretool_gate.os, "write",
                                        lambda fd, data: written.append((fd, data))):
            pretool_gate.watchdog_deny_and_exit("slow")
        self.exit.assert_called_once_with(2)
        self.assertEqual([1, 2], [fd for fd, _ in written])
        self.assertEqual(b"slow\n", written[1][1])

    def test_a_call_that_finishes_in_time_cancels_the_timer(self):
        self.evaluate({"tool_name": "Bash", "tool_input": {"command": "git push origin x"},
                       "agent_id": "a1"}, 0.0)
        self.exit.assert_not_called()

    def test_the_real_process_exits_2_with_the_deny_while_the_main_thread_is_stuck(self):
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(PIPELINE_DIR)!r})\n"
            "import pretool_gate as g\n"
            "g.WATCHDOG_SECONDS = 0.5\n"
            "g.handle_bash = lambda *a, **k: time.sleep(30)\n"
            "raise SystemExit(g.main())\n")
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push origin x"},
                   "agent_id": "a1"}
        started = time.monotonic()
        done = subprocess.run([sys.executable, "-I", "-c", script], input=json.dumps(payload).encode(),
                              capture_output=True, timeout=25)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn(b"did not finish", done.stderr)
        self.assertEqual("deny", json.loads(done.stdout.decode().strip().splitlines()[-1])[
            "hookSpecificOutput"]["permissionDecision"])

    def test_the_default_deadline_is_inside_the_hook_timeout(self):
        unittest.mock.patch.stopall()
        settings = json.loads((state.ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        timeouts = [hook["timeout"] for entry in settings["hooks"]["PreToolUse"]
                    for hook in entry["hooks"] if "pretool_gate.py" in hook["command"]]
        self.assertTrue(timeouts)
        self.assertLess(pretool_gate.WATCHDOG_SECONDS, min(timeouts))
        self.assertLessEqual(pretool_gate.WATCHDOG_SECONDS, 14.0)
        self.assertGreater(pretool_gate.WATCHDOG_SECONDS, pretool_gate.PUSH_BUDGET)


class PlanBlockedUncTest(unittest.TestCase):
    def test_a_network_path_is_not_probed_and_the_callers_cwd_judges_the_branch(self):
        seen = []

        def branch(command, cwd):
            seen.append(cwd)
            return "bugfix/task-9"

        with unittest.mock.patch.object(pretool_gate, "plan_pending_tasks", return_value=["task-9"]), \
                unittest.mock.patch.object(pretool_gate, "current_branch", branch), \
                unittest.mock.patch.object(pretool_gate.Path, "is_dir",
                                           side_effect=AssertionError("probed a network path")):
            task = pretool_gate.plan_blocked_task(path=UNC_DIR + "/x.py", cwd="C:/work")
        self.assertEqual(["C:/work"], seen)
        self.assertEqual("task-9", task)


class LeakDenyTextTest(unittest.TestCase):
    def test_the_deny_names_the_configured_base_not_a_hardcoded_one(self):
        config = {"remote": "upstream", "main_branch": "trunk"}
        with unittest.mock.patch.object(state, "load_pipeline", return_value=config), \
                unittest.mock.patch.object(pretool_gate, "push_repo", return_value="."), \
                unittest.mock.patch.object(pretool_gate, "leak_check",
                                           return_value=(1, [".agentry/memory/x.md"])):
            reason = pretool_gate.push_leak_reason("git push", ".", ["HEAD"])
        self.assertIn("upstream/trunk", reason)
        self.assertNotIn("origin/main", reason)
        self.assertIn(".agentry/memory/x.md", reason)


class LeakGuardBaseTest(unittest.TestCase):
    def setUp(self):
        self.repo = tmproot.sandbox(self, "leak_f2_") / "repo"
        self.repo.mkdir()
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "f2@example.com"], self.repo)
        git(["config", "user.name", "F2"], self.repo)
        (self.repo / "README.md").write_text("x", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        self.seed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo), text=True,
                                   capture_output=True, check=True).stdout.strip()
        leak = self.repo / ".agentry" / "memory" / "lessons.md"
        leak.parent.mkdir(parents=True)
        leak.write_text("secret", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "leak"], self.repo)

    def configured(self, **config):
        return unittest.mock.patch.object(state, "load_pipeline", return_value=config)

    def test_a_remote_named_heads_cannot_turn_the_base_into_the_local_trunk(self):
        git(["update-ref", "refs/remotes/heads/main", self.seed], self.repo)
        with self.configured(remote="heads"):
            code, lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual((1, [".agentry/memory/lessons.md"]), (code, lines))

    def test_a_remote_named_heads_with_no_remote_ref_is_an_error_not_a_pass(self):
        with self.configured(remote="heads"):
            code, _lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual(2, code)

    def test_a_slash_in_the_trunk_is_allowed(self):
        git(["update-ref", "refs/remotes/origin/release/1.0", self.seed], self.repo)
        with self.configured(main_branch="release/1.0"):
            code, lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual((1, [".agentry/memory/lessons.md"]), (code, lines))

    def test_a_remote_or_trunk_outside_the_ref_alphabet_is_refused(self):
        git(["update-ref", "refs/remotes/origin/main", self.seed], self.repo)
        bad = ({"remote": "or igin"}, {"remote": "o:rigin"}, {"remote": "o/rigin"}, {"remote": "$(id)"},
               {"remote": "a;b"}, {"remote": "a\nb"}, {"main_branch": "ma in"},
               {"main_branch": "main^"}, {"main_branch": "main~1"}, {"main_branch": "a:b"},
               {"main_branch": "a*"}, {"main_branch": "a\\b"})
        for config in bad:
            with self.configured(**config):
                code, lines = leak_guard.check("HEAD", self.repo)
            self.assertEqual(2, code, config)
            self.assertIn("plain ref name", lines[0], config)

    def test_the_error_is_exit_2_from_the_command_line_too(self):
        with self.configured(remote="or igin"), contextlib.redirect_stderr(io.StringIO()), \
                unittest.mock.patch.object(leak_guard.Path, "cwd", return_value=self.repo):
            self.assertEqual(2, leak_guard.main(["--ref", "HEAD"]))


class GateHardeningTest(unittest.TestCase):
    def test_taskkill_is_called_by_its_absolute_path(self):
        with unittest.mock.patch.dict(os.environ, {"SystemRoot": r"D:\Win"}):
            self.assertEqual(os.path.join(r"D:\Win", "System32", "taskkill.exe"), gate.taskkill_path())
        self.assertTrue(os.path.isabs(gate.taskkill_path()) or os.name != "nt")

    @unittest.skipUnless(os.name == "nt", "taskkill is the Windows kill path")
    def test_kill_tree_runs_the_absolute_taskkill(self):
        proc = unittest.mock.Mock(pid=4242)
        with unittest.mock.patch.object(gate.subprocess, "run") as run:
            gate.kill_tree(proc)
        argv = run.call_args.args[0]
        self.assertNotEqual("taskkill", argv[0])
        self.assertTrue(os.path.isabs(argv[0]))
        self.assertTrue(argv[0].lower().endswith("taskkill.exe"))
        self.assertEqual(["/T", "/F", "/PID", "4242"], argv[1:])

    def pipeline(self) -> dict:
        return {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": "echo {task}"}}]}}}

    def test_a_task_id_that_is_not_task_digits_never_reaches_the_shell(self):
        with unittest.mock.patch.object(gate, "run_command",
                                        side_effect=AssertionError("a shell command was built")):
            for task in ("task-1; echo pwned", "task-1 && calc", "$(id)", "task-", "TASK-1",
                         "task-1\n", "task-\uff11", "../task-1", "task-1`id`", "x"):
                result = gate.run_gate(self.pipeline(), "test", task)
                self.assertFalse(result["passed"], task)
                self.assertFalse(result["configured"], task)
                self.assertIn("task id", result["output"], task)

    def test_a_plain_task_id_is_spliced_in(self):
        with unittest.mock.patch.object(gate, "run_command", return_value=(0, "ok")) as run, \
                unittest.mock.patch.object(gate.state, "emit_gate"):
            result = gate.run_gate(self.pipeline(), "test", "task-0800")
        self.assertTrue(result["passed"])
        self.assertEqual("echo task-0800", run.call_args.args[0])

    def test_no_task_still_runs_the_command_with_an_empty_id(self):
        with unittest.mock.patch.object(gate, "run_command", return_value=(0, "ok")) as run:
            gate.run_gate(self.pipeline(), "test")
        self.assertEqual("echo ", run.call_args.args[0])


class CleanReasonTest(unittest.TestCase):
    def test_separators_private_use_unassigned_and_surrogates_become_spaces(self):
        for ch in ("\u2028", "\u2029", "\ue000", "\U000f0000", "\u0378", "\ud800", "\udfff"):
            self.assertEqual("a b", approve.clean_reason(f"a{ch}b"), repr(ch))

    def test_ordinary_text_with_accents_and_symbols_is_kept(self):
        text = "caf\u00e9 \u00fcber \u4e2d\u6587 100% done (see #12)"
        self.assertEqual(text, approve.clean_reason(text))

    def test_a_reason_made_only_of_those_characters_is_empty(self):
        self.assertEqual("", approve.clean_reason("\u2028\u2029\ue000\ud800 "))


if __name__ == "__main__":
    unittest.main()

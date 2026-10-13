"""task-0124 security fix round, part F1: the subagent gate (agent_gate.py).

  * HIGH 1  a glob walk is bounded by time, not only by hit count. A pattern with many
            wildcard levels over a big tree yields nothing for minutes, and a hook that
            times out is an ALLOW. glob_hits() now raises GlobDeadline and every profile
            refuses the call.
  * HIGH 2  main() arms a watchdog timer shorter than the hook timeout: it writes the deny
            and ends the process from its own thread, so a main thread stuck in a syscall
            cannot turn into an allow.
  * MED 5   a word with THREE or more leading separators is a network path too (Git Bash
            collapses `///host/share`), and the readonly and docs profiles refuse a network
            path as an argument of any allowed program that opens paths.
  * MED 6   the dev profile cannot write a ref by hand: `git update-ref`, and a `git fetch`
            whose refspec lands under refs/remotes/.

The real-process tests run the gate in a child interpreter with the deadline or the timer
shrunk, so the whole path (stdin payload, exit status, stdout, stderr) is the one Claude Code
starts.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parents[0] / "pipeline"
for _dir in (PIPELINE_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import agent_gate
import pretool_gate
import state
from test_subagent_enforcement import bash, make_project

EXPLOIT = "true > C:/Windows/*/*/*/*/*/zz? ; echo auto > .agentry/state/approvals"

# Runs agent_gate.main() in a child with the hang under test injected and one constant shrunk.
CHILD = """
import glob, sys, time
sys.path.insert(0, {pipeline!r})
import agent_gate
{setup}
sys.argv = ["agent_gate.py", "--profile", {profile!r}]
raise SystemExit(agent_gate.main())
"""
SLOW_GLOB = """
agent_gate.GLOB_DEADLINE_SECONDS = 0.3
def slow(pattern, *args, **kwargs):
    time.sleep(40)
    yield from ()
glob.iglob = slow
"""
STUCK_EVALUATE = """
agent_gate.WATCHDOG_SECONDS = 0.3
agent_gate.evaluate = lambda: time.sleep(40)
"""
SHRUNK_TIMER_REAL_EVALUATE = "agent_gate.WATCHDOG_SECONDS = 0.5"


def run_child(setup: str, profile: str, payload: dict) -> tuple[subprocess.CompletedProcess, float]:
    code = CHILD.format(pipeline=str(PIPELINE_DIR), setup=setup, profile=profile)
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", code], input=json.dumps(payload).encode("utf-8"),
                          capture_output=True, timeout=60)
    return done, time.monotonic() - started


def bash_payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(state.ROOT)}


class GlobDeadlineTest(unittest.TestCase):
    def setUp(self):
        self.proj = make_project(self)

    def slow_iglob(self):
        def slow(pattern, *args, **kwargs):
            time.sleep(10)
            yield from ()
        return unittest.mock.patch.object(agent_gate.glob, "iglob", slow)

    def test_a_walk_that_yields_nothing_for_too_long_raises_instead_of_waiting(self):
        started = time.monotonic()
        with unittest.mock.patch.object(agent_gate, "GLOB_DEADLINE_SECONDS", 0.05), self.slow_iglob():
            with self.assertRaises(agent_gate.GlobDeadline):
                agent_gate.glob_hits("C:/Windows/*/*/*/*/*/zz?")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_pattern_that_finishes_in_time_returns_its_hits(self):
        hits = agent_gate.glob_hits(str(self.proj / "docs" / "*.md"))
        self.assertEqual(["a.md"], [Path(h).name for h in hits])

    def test_the_hit_cap_still_applies(self):
        for n in range(agent_gate.MAX_GLOB_HITS + 10):
            (self.proj / "app" / f"f{n}.txt").write_text("x", encoding="utf-8")
        hits = agent_gate.glob_hits(str(self.proj / "app" / "*.txt"))
        self.assertEqual(agent_gate.MAX_GLOB_HITS, len(hits))

    def test_the_deadline_is_not_an_error_the_callers_swallow(self):
        self.assertFalse(issubclass(agent_gate.GlobDeadline, (OSError, ValueError)))
        with unittest.mock.patch.object(agent_gate, "glob_hits", side_effect=agent_gate.GlobDeadline("p")):
            with self.assertRaises(agent_gate.GlobDeadline):
                agent_gate.protected_operand("src/*/x", [str(self.proj)])

    def test_the_deadline_error_reaches_evaluate_for_every_profile_even_dev(self):
        for profile in ("dev", "readonly", "docs"):
            with self.subTest(profile=profile):
                done, elapsed = run_child(SLOW_GLOB, profile, bash_payload(EXPLOIT))
                self.assertEqual(2, done.returncode, done.stderr.decode("utf-8", "replace"))
                self.assertIn("wildcard", done.stderr.decode("utf-8", "replace"))
                self.assertLess(elapsed, 20)

    def test_a_dev_write_with_a_fast_glob_is_still_judged_on_the_file_it_names(self):
        code, err = bash("dev", "echo x > .agentry/state/*", str(self.proj))
        self.assertEqual(2, code)
        self.assertIn(".agentry/state", err)

    def test_the_unc_glob_is_still_never_expanded(self):
        with unittest.mock.patch.object(agent_gate.glob, "iglob",
                                        side_effect=AssertionError("globbed a network path")):
            for pattern in ("//server/share/*", "///server/share/*", "\\\\server\\share\\*"):
                with self.subTest(pattern=pattern):
                    self.assertEqual([], agent_gate.glob_hits(pattern))


class WatchdogTest(unittest.TestCase):
    def test_the_timer_leaves_margin_for_interpreter_start_inside_the_hook_timeout(self):
        self.assertLessEqual(agent_gate.WATCHDOG_SECONDS, 14.0)
        self.assertGreater(agent_gate.WATCHDOG_SECONDS, agent_gate.GLOB_DEADLINE_SECONDS)

    def test_the_timer_denies_through_the_shared_helper_with_the_gates_own_message(self):
        with unittest.mock.patch.object(pretool_gate, "watchdog_deny_and_exit") as helper:
            agent_gate.watchdog_fire()
        helper.assert_called_once_with(agent_gate.WATCHDOG_MSG)

    def test_the_shared_helper_writes_a_pretooluse_deny_without_the_stream_locks(self):
        written = {}
        locked = unittest.mock.Mock()
        locked.write.side_effect = AssertionError("used a locked stream")
        with unittest.mock.patch.object(pretool_gate.os, "write",
                                        lambda fd, data: written.setdefault(fd, data)), \
                unittest.mock.patch.object(pretool_gate.os, "_exit") as leave, \
                unittest.mock.patch.object(sys, "stdout", locked), \
                unittest.mock.patch.object(sys, "stderr", locked):
            pretool_gate.watchdog_deny_and_exit("slow")
        leave.assert_called_once_with(2)
        decision = json.loads(written[1].decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual("PreToolUse", decision["hookEventName"])
        self.assertEqual("deny", decision["permissionDecision"])
        self.assertEqual("slow", decision["permissionDecisionReason"])
        self.assertEqual(b"slow\n", written[2])

    def test_a_timer_that_cannot_start_denies_instead_of_running_unguarded(self):
        written = []
        with unittest.mock.patch.object(agent_gate.threading.Timer, "start",
                                        side_effect=RuntimeError("can't start new thread")), \
                unittest.mock.patch.object(pretool_gate.os, "write",
                                           lambda fd, data: written.append(fd)), \
                unittest.mock.patch.object(pretool_gate.os, "_exit") as leave, \
                unittest.mock.patch.object(agent_gate, "evaluate", return_value=0):
            agent_gate.main()
        leave.assert_called_with(2)
        self.assertEqual([1, 2], written)

    def test_a_gate_stuck_in_a_syscall_is_denied_by_the_timer_with_exit_2(self):
        for profile in ("dev", "readonly", "docs"):
            with self.subTest(profile=profile):
                done, elapsed = run_child(STUCK_EVALUATE, profile, bash_payload("ls"))
                self.assertEqual(2, done.returncode)
                out = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
                self.assertEqual("deny", out["hookSpecificOutput"]["permissionDecision"])
                self.assertIn("did not finish", done.stderr.decode("utf-8", "replace"))
                self.assertLess(elapsed, 20)

    def test_a_call_that_finishes_in_time_is_not_touched_by_the_timer(self):
        done, elapsed = run_child(SHRUNK_TIMER_REAL_EVALUATE, "readonly", bash_payload("ls"))
        self.assertEqual(0, done.returncode, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(b"", done.stdout)
        self.assertLess(elapsed, 20)

    def test_a_normal_deny_keeps_its_own_reason_and_status(self):
        done, _ = run_child(SHRUNK_TIMER_REAL_EVALUATE, "readonly", bash_payload("python -c 1"))
        self.assertEqual(2, done.returncode)
        self.assertIn(agent_gate.ALLOWLIST_PHRASE, done.stderr.decode("utf-8", "replace"))

    def test_main_cancels_its_timer_when_the_check_ends(self):
        timers = []
        real = agent_gate.threading.Timer

        def spy(*args, **kwargs):
            timers.append(real(*args, **kwargs))
            return timers[-1]

        with unittest.mock.patch.object(agent_gate.threading, "Timer", spy), \
                unittest.mock.patch.object(agent_gate, "evaluate", return_value=0):
            self.assertEqual(0, agent_gate.main())
        self.assertEqual(1, len(timers))
        self.assertTrue(timers[0].daemon)
        self.assertTrue(timers[0].finished.is_set())


class UncSpellingTest(unittest.TestCase):
    def setUp(self):
        self.proj = make_project(self)

    def test_three_or_more_leading_separators_are_a_network_path(self):
        for path in ("//host/share", "///host/share", "////host/share", "\\\\host\\share",
                     "\\\\\\host\\share", "/\\/host/share", "'///host/share'", "  ///host/share",
                     "\\\\?\\C:\\x"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.is_unc(path))

    def test_local_roots_and_plain_text_are_not(self):
        for path in ("/", "//", "///", "/x/y", "C:/x", "docs/a.md", "a//b", ""):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_unc(path))

    def test_readonly_and_docs_refuse_a_network_path_as_an_argument_of_an_allowed_program(self):
        commands = (
            "cat ///host/share/x", "cat //host/share/x", "cat '////host/share/x'", "ls ///host/share",
            "head -n 1 ///host/share/x", "diff docs/a.md ///host/share/b", "stat ///host/share/x",
            "grep needle ///host/share/x", "grep -f ///host/share/patterns docs/a.md",
            "rg needle --ignore-file=///host/share/f", "wc -l ///host/share/x",
            "git -C ///host/share log", "git log -- ///host/share/x", "cd ///host/share && ls",
            "find ///host/share -name x", "test -e ///host/share/x", "realpath ///host/share/x",
            "file ///host/share/x", "du ///host/share", "jq . ///host/share/x.json",
            "grep -f//host/share/patterns docs/a.md", "grep -f///host/share/patterns docs/a.md",
            "grep -rf//host/share/patterns docs", "rg --ignore-file=//host/share/f needle",
            "rg -f//host/share/f needle docs",
        )
        for profile in ("readonly", "docs"):
            for command in commands:
                with self.subTest(profile=profile, command=command):
                    self.assertIn("network path", agent_gate.program_denied(command, profile))
                    code, err = bash(profile, command, str(self.proj))
                    self.assertEqual(2, code)
                    self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)

    def test_the_backslash_spelling_of_a_network_path_is_refused_quoted_and_glued(self):
        commands = (
            r"cat '\\host\share\x'", r'cat "\\\\host\\share\\x"',r"ls '\\host\share'",
            r"head -n 1 '\\host\share\x'", r"grep needle '\\host\share\x'",
            r"rg needle --ignore-file='\\host\share\f'", r"cat '\\?\UNC\host\share\x'",
            r"grep -f'\\host\share\p' docs/a.md", r"cat '\\\host\share\x'",
            r"diff docs/a.md '\\host\share\b'", r"test -e '\\host\share\x'",
        )
        for profile in ("readonly", "docs"):
            for command in commands:
                with self.subTest(profile=profile, command=command):
                    self.assertIn("network path", agent_gate.program_denied(command, profile))
                    code, _err = bash(profile, command, str(self.proj))
                    self.assertEqual(2, code)

    def test_unc_argument_judges_the_word_the_option_value_and_the_glued_operand(self):
        for args in (["//h/s/p"], ["--ignore-file=//h/s/p"], ["-f//h/s/p"], ["-rf///h/s/p"],
                     [r"\\h\s\p"], [r"--file=\\h\s\p"], [r"-f\\h\s\p"], ["'//h/s/p'"]):
            with self.subTest(args=args):
                self.assertEqual(args[0], agent_gate.unc_argument("grep", args))

    def test_search_patterns_and_printed_words_that_only_look_like_slashes_pass(self):
        for command in ("grep '// TODO' docs/a.md", "rg '//' docs", "grep -e '//hostlike' docs/a.md",
                        "echo //host/share", "printf '%s' ///host", "grep -n http://x docs/a.md",
                        "cat docs/a.md", "ls docs", "git log -- docs/a.md"):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.program_denied(command, "readonly"))

    def test_a_planted_binary_on_a_network_path_is_still_refused(self):
        for token in ("///host/share/ls", "//host/share/ls"):
            self.assertTrue(agent_gate.planted_binary(token, str(self.proj)))


class DevRefWriteTest(unittest.TestCase):
    DENIED = (
        "git update-ref refs/remotes/origin/main HEAD",
        "git update-ref -d refs/remotes/origin/main",
        "git -C . update-ref refs/heads/x HEAD",
        "git fetch . +HEAD:refs/remotes/origin/main",
        "git fetch . HEAD:refs/remotes/origin/main",
        "git fetch -f . HEAD:refs/remotes/origin/main",
        "git fetch . '+refs/heads/*:refs/remotes/origin/*'",
        "git fetch . +HEAD:refs/remotes",
        "git fetch origin --refmap='+refs/heads/*:refs/remotes/o/*' 'refs/heads/*'",
        "git fetch origin --refmap +refs/heads/*:refs/remotes/o/* refs/heads/*",
        "git fetch . HEAD:REFS/REMOTES/origin/main",
        "bash -c 'git update-ref refs/remotes/origin/main HEAD'",
        "true && git fetch . +HEAD:refs/remotes/origin/main",
        "(cd app && git update-ref refs/remotes/origin/main HEAD)",
        "git pull . +HEAD:refs/remotes/origin/main",
        "git pull origin HEAD:refs/remotes/origin/main",
        "git pull --rebase . HEAD:remotes/origin/main",
        "git fetch . +HEAD:remotes/origin/main",
        "git fetch . HEAD:REMOTES/origin/main",
        "git fetch . HEAD:remotes",
        "git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main",
        "git symbolic-ref refs/remotes/origin/HEAD refs/heads/x",
        "git symbolic-ref remotes/origin/HEAD refs/heads/x",
        "git symbolic-ref HEAD refs/remotes/origin/main",
        "git symbolic-ref -d refs/remotes/origin/HEAD",
        "git push . HEAD:refs/remotes/origin/main",
        "git push . +HEAD:remotes/origin/main",
    )
    ALLOWED = (
        "git fetch", "git fetch origin", "git fetch --all --prune", "git fetch origin main",
        "git fetch origin +refs/heads/main:refs/heads/tmp-main", "git fetch . refs/remotes/origin/main",
        "git fetch C:/work/repo", "git log refs/remotes/origin/main", "git status",
        "git branch -r", "git show-ref refs/remotes/origin/main",
        "git pull", "git pull origin main", "git pull --rebase origin main",
        "git fetch . remotes/origin/main", "git symbolic-ref HEAD", "git symbolic-ref --short HEAD",
        "git symbolic-ref refs/remotes/origin/HEAD", "git push origin HEAD",
        "git push -u origin bugfix/task-1", "git push origin HEAD:refs/heads/bugfix/task-1",
    )

    def setUp(self):
        self.proj = make_project(self)

    def test_update_ref_and_a_fetch_into_remote_tracking_refs_are_named(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                self.assertNotEqual("", agent_gate.dev_ref_write(command))

    def test_the_ordinary_fetches_and_reads_are_left_alone(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.dev_ref_write(command))

    def test_the_dev_profile_denies_them_with_a_reason_about_refs(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                code, err = bash("dev", command, str(self.proj))
                self.assertEqual(2, code)
                self.assertIn("refs/remotes", err)

    def test_the_dev_profile_still_runs_a_plain_fetch_past_this_check(self):
        for command in ("git fetch origin", "git fetch --all --prune"):
            with self.subTest(command=command):
                self.assertNotIn("refs/remotes", bash("dev", command, str(self.proj))[1])

    def test_readonly_and_docs_refuse_the_same_commands_through_the_git_allowlist(self):
        for profile in ("readonly", "docs"):
            for command in ("git update-ref refs/remotes/origin/main HEAD",
                            "git fetch . +HEAD:refs/remotes/origin/main"):
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, str(self.proj))
                    self.assertEqual(2, code)
                    self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)


if __name__ == "__main__":
    unittest.main()

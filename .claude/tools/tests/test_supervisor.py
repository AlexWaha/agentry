"""The external supervisor: its detector, its two hard caps, and its refusals.

The supervisor is the one piece of this harness that acts with nobody watching,
so the tests are weighted towards what it must NOT do:

1. the four classifications, each from the fact it is supposed to be derived
   from (and the precedence between them, which is what keeps a loop out of the
   relaunch path);
2. DEAD detected by process liveness with no timeout elapsed - the criterion
   from task-0058 verbatim: start a run, kill the owning process, watch the
   classification flip;
3. LOOPING never relaunched, pinned by a test that also proves the guard is
   load-bearing: with RELAUNCHABLE widened to include LOOPING the spawn DOES
   happen, so the assertion in the test above it is not vacuous;
4. both caps, and that exceeding either STOPS the supervisor rather than
   skipping the action and polling on;
5. fail-safe direction - the opposite of every hook in this tree. A failure in
   the supervisor must leave pipeline state byte-identical;
6. no push, no merge, no approval, by source inspection and by running a real
   relaunch pass against a real run row and reading the row back.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import approvals
import pretool_gate
import state
import supervisor

# Dash characters are built from code points so that this file, which is itself
# scanned by DashTest below, cannot contain the characters it forbids.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def cfg(**over) -> dict:
    """A config built from the shipped DEFAULTS, never a hand-typed copy: a
    literal here would keep asserting against a default that had moved."""
    out = dict(supervisor.DEFAULTS)
    out["continuation_ceiling"] = 30
    out.update(over)
    return out


def row(task="task-0001", stage="implement", status=state.ST_IN_PROGRESS,
        awaiting_human="", updated=None, **extra) -> dict:
    """A run.db row as all_runs() hands it over. `updated` defaults to now, so a
    row is HEALTHY unless a test deliberately ages it."""
    out = {
        "task": task, "type": "feature", "stage": stage, "stage_status": status,
        "awaiting_human": awaiting_human, "commit_approved": 0, "push_approved": 0,
        "retries": 0, "continuations": 0, "pipeline": state.BUILD,
        "updated": updated if updated is not None else state.now(),
    }
    out.update(extra)
    return out


def stamp(ts: float) -> str:
    """An epoch value in run.db's `updated` format."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aged(seconds: float) -> str:
    """An `updated` stamp that many seconds in the past, in run.db's format."""
    return stamp(time.time() - seconds)


def spawn_child() -> subprocess.Popen:
    """A real, long-lived child process to own a run. Real rather than a fake
    pid because the whole point of the DEAD test is that liveness is observed
    from the operating system."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return proc


class Sandbox(unittest.TestCase):
    """Each test gets its own state directory, run store and registry, so
    nothing here can read or write the real pipeline. notify() is captured
    rather than silenced, because several tests assert on what was surfaced."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for attr, value in (("STATE_DIR", self.tmp), ("DB_PATH", self.tmp / "run.db")):
            patcher = unittest.mock.patch.object(state, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notices: list[str] = []
        patcher = unittest.mock.patch.object(supervisor, "notify", self.notices.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_row(self, task="task-0001", stage="implement", **fields) -> dict:
        """A REAL row in the sandboxed run store, for the tests that have to read
        pipeline state back after the supervisor ran."""
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage, state.BUILD)
            if fields:
                state.set_fields(conn, task, **fields)
            return state.get_run(conn, task)
        finally:
            conn.close()

    def read_row(self, task="task-0001") -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()


class ClassificationTest(Sandbox):
    """The four labels, and the order they are checked in."""

    def test_healthy_when_the_row_changed_recently(self):
        label, why = supervisor.classify(row(), {}, cfg(), time.time())
        self.assertEqual(supervisor.HEALTHY, label)
        self.assertIn("implement", why)

    def test_healthy_when_parked_on_a_human_however_old(self):
        # A task waiting for the CEO's commit approval is correct, not stalled.
        # Getting this wrong would relaunch a session while he reads the diff.
        for awaiting in ("commit", "push", "merge"):
            with self.subTest(awaiting_human=awaiting):
                label, why = supervisor.classify(
                    row(stage="ready", awaiting_human=awaiting, updated=aged(99999)),
                    {}, cfg(), time.time())
                self.assertEqual(supervisor.HEALTHY, label)
                self.assertIn("by design", why)

    def test_healthy_when_already_blocked_or_done(self):
        for kind, r in (("blocked", row(status=state.ST_BLOCKED, updated=aged(99999))),
                        ("done", row(stage="done", updated=aged(99999)))):
            with self.subTest(kind=kind):
                label, _ = supervisor.classify(r, {}, cfg(), time.time())
                self.assertEqual(supervisor.HEALTHY, label)

    def test_stalled_when_the_row_has_not_changed_for_stall_seconds(self):
        label, why = supervisor.classify(
            row(updated=aged(1300)), {}, cfg(stall_seconds=1200), time.time())
        self.assertEqual(supervisor.STALLED, label)
        self.assertIn("unchanged", why)

    def test_not_stalled_before_the_threshold(self):
        label, _ = supervisor.classify(
            row(updated=aged(1100)), {}, cfg(stall_seconds=1200), time.time())
        self.assertEqual(supervisor.HEALTHY, label)

    def test_looping_from_continuations_climbing_on_an_unchanged_stage(self):
        # Fresh row, so no timeout is involved: the fact is the climb itself.
        entry = {"loop_ticks": 2, "stage": "implement", "continuations": 7}
        label, why = supervisor.classify(row(continuations=7), entry, cfg(loop_ticks=2),
                                         time.time())
        self.assertEqual(supervisor.LOOPING, label)
        self.assertIn("consecutive polls", why)

    def test_looping_from_the_pipelines_own_continuation_ceiling(self):
        label, why = supervisor.classify(row(continuations=30), {}, cfg(continuation_ceiling=30),
                                         time.time())
        self.assertEqual(supervisor.LOOPING, label)
        self.assertIn("continuation_ceiling", why)

    def test_looping_wins_over_stalled(self):
        entry = {"loop_ticks": 5, "stage": "implement", "continuations": 9}
        label, _ = supervisor.classify(row(continuations=9, updated=aged(99999)), entry,
                                       cfg(), time.time())
        self.assertEqual(supervisor.LOOPING, label)

    def test_looping_wins_over_dead(self):
        # The precedence that matters most: a looping run whose owner also died
        # must not be labelled DEAD, because DEAD is relaunchable and LOOPING is
        # the one thing a relaunch must never touch.
        proc = spawn_child()
        proc.terminate()
        proc.wait(timeout=30)
        entry = {"loop_ticks": 3, "stage": "implement", "continuations": 9,
                 "pid": proc.pid, "spawn_stage": "implement"}
        label, _ = supervisor.classify(row(continuations=9), entry, cfg(), time.time())
        self.assertEqual(supervisor.LOOPING, label)

    def test_a_run_with_no_recorded_owner_pid_is_never_dead(self):
        # Honest limitation, pinned so nobody assumes otherwise: the supervisor
        # only knows the pids it spawned itself, so an externally started
        # session can only ever be STALLED.
        label, _ = supervisor.classify(row(updated=aged(99999)), {}, cfg(), time.time())
        self.assertEqual(supervisor.STALLED, label)

    def test_dead_needs_the_run_to_still_be_at_the_stage_it_was_spawned_for(self):
        proc = spawn_child()
        proc.terminate()
        proc.wait(timeout=30)
        entry = {"pid": proc.pid, "spawn_stage": "implement", "stage": "test",
                 "continuations": 0}
        # The spawned session finished 'implement' and the run moved on, so its
        # exit is a completed job, not a death.
        label, _ = supervisor.classify(row(stage="test"), entry, cfg(), time.time())
        self.assertEqual(supervisor.HEALTHY, label)

    def test_classify_only_ever_returns_one_of_the_four(self):
        cases = [
            (row(), {}),
            (row(updated=aged(99999)), {}),
            (row(continuations=99), {}),
            (row(stage="done"), {}),
            (row(status=state.ST_BLOCKED), {}),
            (row(awaiting_human="commit"), {}),
            (row(updated="not-a-timestamp"), {}),
        ]
        for i, (r, entry) in enumerate(cases):
            with self.subTest(case=i):
                label, _ = supervisor.classify(r, entry, cfg(), time.time())
                self.assertIn(label, supervisor.CLASSIFICATIONS)


class DeadSessionTest(Sandbox):
    """The task-0058 criterion: start a run, kill the owning process, assert the
    classification flips WITHOUT a timeout elapsing."""

    def test_killing_the_owning_process_flips_healthy_to_dead(self):
        proc = spawn_child()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        conf = cfg(stall_seconds=99999)  # no timeout can possibly elapse
        r = row()
        entry = {"pid": proc.pid, "spawn_stage": "implement", "stage": "implement",
                 "continuations": 0}

        label, why = supervisor.classify(r, entry, conf, time.time())
        self.assertEqual(supervisor.HEALTHY, label, why)
        self.assertIn("alive", why)

        proc.terminate()
        proc.wait(timeout=30)

        label, why = supervisor.classify(r, entry, conf, time.time())
        self.assertEqual(supervisor.DEAD, label, why)
        self.assertIn("exited", why)
        # Proof that no clock was involved: the row is younger than the stall
        # threshold by four orders of magnitude, and the row never changed
        # between the two calls.
        self.assertLess(supervisor.idle_seconds(r, time.time()), conf["stall_seconds"])

    def test_pid_alive_is_true_for_this_process_and_false_for_a_reaped_child(self):
        self.assertTrue(supervisor.pid_alive(os.getpid()))
        proc = spawn_child()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertTrue(supervisor.pid_alive(proc.pid))
        # The probe must not be lethal. It is not, on either platform, and not
        # for the reason usually given either: os.kill(pid, 0) on Windows does
        # NOT kill (measured - see pid_alive's docstring), only signal 9 does.
        # The assertion stays because it is cheap and it is the thing that
        # would catch a future probe that does terminate the target.
        self.assertIsNone(proc.poll())
        proc.terminate()
        proc.wait(timeout=30)
        self.assertFalse(supervisor.pid_alive(proc.pid))

    def test_pid_alive_rejects_junk_without_raising(self):
        for value in (0, -1, None, "", "abc"):
            with self.subTest(pid=value):
                self.assertFalse(supervisor.pid_alive(value))

    @unittest.skipUnless(os.name == "nt", "Windows access-right behaviour")
    def test_a_live_process_we_do_not_own_reads_alive_not_dead(self):
        # THE reason the Windows branch uses kernel32: a process the caller does
        # not own cannot be opened for inspection (measured GetLastError 5,
        # ERROR_ACCESS_DENIED, for pid 4 and for services.exe), and reading that
        # refusal as "no such process" would report a live session DEAD - which
        # for the supervisor means relaunching over it. pid 4 is the System
        # process: always running, never ours.
        self.assertNotEqual(supervisor._ERROR_ACCESS_DENIED,
                            supervisor._ERROR_INVALID_PARAMETER)
        self.assertTrue(supervisor.pid_alive(4),
                        "the System process must never read as dead")

    @unittest.skipUnless(os.name == "nt", "Windows error-code branch")
    def test_a_pid_that_never_existed_reads_dead(self):
        # The other side of the same branch: ERROR_INVALID_PARAMETER (measured
        # 87) is the only OpenProcess failure that means dead. Without the
        # distinction this returns True and no DEAD run is ever detected.
        self.assertFalse(supervisor.pid_alive(999999))

    def test_the_windows_branch_goes_through_kernel32_not_os_kill(self):
        src = (PIPELINE_DIR / "supervisor.py").read_text(encoding="utf-8")
        body = src[src.index("def pid_alive"):src.index("def empty_registry")]
        self.assertIn("OpenProcess", body)
        self.assertIn("GetExitCodeProcess", body)
        # The only os.kill CALL in the function sits after the Windows branch
        # opens, which is what makes it the POSIX path.
        self.assertLess(body.index('if os.name == "nt"'), body.rindex("os.kill(pid, 0)"))


class ObserveTest(Sandbox):
    """loop_ticks is the only derived fact, so it gets its own tests."""

    def test_tick_increments_when_continuations_climb_on_the_same_stage(self):
        entry = {}
        supervisor.observe(entry, row(continuations=1), time.time())
        self.assertEqual(0, entry.get("loop_ticks", 0))
        supervisor.observe(entry, row(continuations=2), time.time())
        self.assertEqual(1, entry["loop_ticks"])
        supervisor.observe(entry, row(continuations=3), time.time())
        self.assertEqual(2, entry["loop_ticks"])

    def test_a_stage_change_resets_the_tick(self):
        entry = {}
        supervisor.observe(entry, row(continuations=1), time.time())
        supervisor.observe(entry, row(continuations=2), time.time())
        self.assertEqual(1, entry["loop_ticks"])
        supervisor.observe(entry, row(stage="test", continuations=0), time.time())
        self.assertEqual(0, entry["loop_ticks"])

    def test_no_tick_when_nothing_changed(self):
        entry = {}
        for _ in range(4):
            supervisor.observe(entry, row(continuations=5), time.time())
        self.assertEqual(0, entry.get("loop_ticks", 0))

    def test_a_poll_with_no_climb_resets_the_tick(self):
        """The count is CONSECUTIVE, and classify()'s reason string says so.

        MEASURED DEFECT (task-0058, third round): there was no branch resetting
        the counter on a quiet poll, so it accumulated across the whole stage.
        Climbs at poll 1 and poll 9, with eight quiet polls between them, gave
        loop_ticks=2 and the line "continuations climbed on 2 consecutive polls"
        - an assertion of adjacency the code never measured, and at the default
        threshold of 2 it parked a healthy task BLOCKED for two Stop-hook nags
        anywhere inside one stage. The test above starts from a fresh entry, so
        it only ever proved that 0 stays 0; this one goes climb -> quiet.
        """
        entry = {}
        supervisor.observe(entry, row(continuations=1), time.time())
        supervisor.observe(entry, row(continuations=2), time.time())
        self.assertEqual(1, entry["loop_ticks"])
        # Same stage, same continuations: the run of adjacent climbs is broken.
        supervisor.observe(entry, row(continuations=2), time.time())
        self.assertEqual(0, entry["loop_ticks"])
        # And it counts up again from there rather than resuming at 1.
        supervisor.observe(entry, row(continuations=3), time.time())
        self.assertEqual(1, entry["loop_ticks"])

    def test_eight_quiet_polls_between_two_climbs_are_not_a_loop(self):
        # The exact series the defect was measured with, end to end through
        # classify(): the label must be HEALTHY, not LOOPING.
        entry = {}
        base = time.time()
        series = [1, 2, 2, 2, 2, 2, 2, 2, 2, 3]
        label, why = supervisor.HEALTHY, ""
        for i, cont in enumerate(series):
            now_ts = base + i * 60
            r = row(continuations=cont, updated=stamp(now_ts))
            supervisor.observe(entry, r, now_ts)
            label, why = supervisor.classify(r, entry, cfg(loop_ticks=2), now_ts)
        self.assertEqual(supervisor.HEALTHY, label, why)
        self.assertEqual(1, entry["loop_ticks"])


class LoopingIsNeverRelaunchedTest(Sandbox):
    """The non-negotiable guard, and proof that the guard is load-bearing."""

    def test_looping_is_not_in_the_relaunchable_set(self):
        self.assertNotIn(supervisor.LOOPING, supervisor.RELAUNCHABLE)
        # Control: the set is not simply empty.
        self.assertEqual({supervisor.STALLED, supervisor.DEAD}, set(supervisor.RELAUNCHABLE))

    def test_relaunch_refuses_looping_without_spawning_anything(self):
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            spawned, argv, pid = supervisor.relaunch(
                supervisor.LOOPING, "task-0001", "implement", "loop", cfg())
        self.assertFalse(spawned)
        self.assertEqual([], argv)
        self.assertEqual(0, pid)
        popen.assert_not_called()
        self.assertTrue(any("refused to relaunch" in n for n in self.notices))

    def test_the_guard_is_load_bearing(self):
        # Break it and the spawn happens. This is what makes the test above a
        # real assertion rather than a description: widening RELAUNCHABLE to
        # include LOOPING is the only change made, and Popen is then called.
        with unittest.mock.patch.object(
                supervisor, "RELAUNCHABLE",
                frozenset({supervisor.STALLED, supervisor.DEAD, supervisor.LOOPING})), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            popen.return_value = unittest.mock.Mock(pid=4242)
            spawned, _, pid = supervisor.relaunch(
                supervisor.LOOPING, "task-0001", "implement", "loop", cfg())
        self.assertTrue(spawned)
        self.assertEqual(4242, pid)
        popen.assert_called_once()

    def test_handle_run_parks_a_looping_run_and_spawns_nothing(self):
        self.make_row("task-0001", continuations=9)
        reg = supervisor.empty_registry()
        entry = supervisor.entry_for(reg, "task-0001")
        # The entry's `continuations` is one BELOW the row's, so the poll
        # handle_run is about to make observes a real climb. loop_ticks counts
        # consecutive polls, so a fixture where nothing climbs is a fixture
        # where the count correctly resets - see ObserveTest.
        entry.update({"loop_ticks": 3, "stage": "implement", "continuations": 8})
        r = row(continuations=9)

        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            event = supervisor.handle_run(r, reg, cfg(), time.time(), act=True)

        popen.assert_not_called()
        self.assertEqual(supervisor.LOOPING, event["classification"])
        self.assertEqual(supervisor.ACTION_PARK, event["action"])
        self.assertEqual(state.ST_BLOCKED, self.read_row()["stage_status"])
        # Parked, not advanced: the stage is exactly where it was.
        self.assertEqual("implement", self.read_row()["stage"])


class RelaunchTest(Sandbox):
    """What a relaunch actually does, and what it refuses to do."""

    def test_a_stalled_run_is_relaunched_with_a_logged_command(self):
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            popen.return_value = unittest.mock.Mock(pid=777)
            event = supervisor.handle_run(row(updated=aged(99999)), reg, cfg(),
                                          time.time(), act=True)
        self.assertEqual(supervisor.STALLED, event["classification"])
        self.assertEqual(supervisor.ACTION_RELAUNCH, event["action"])
        self.assertEqual(777, event["pid"])
        argv = popen.call_args[0][0]
        self.assertEqual(event["argv"], argv)
        self.assertEqual(["claude", "-p"], argv[:2])
        self.assertIn("task-0001", argv[2])
        # Logged exactly, not summarised.
        self.assertTrue(any(str(argv) in n for n in self.notices))
        # The spawned pid is remembered, which is what makes DEAD detectable next
        # poll instead of another blind relaunch.
        self.assertEqual(777, reg["tasks"]["task-0001"]["pid"])
        self.assertEqual("implement", reg["tasks"]["task-0001"]["spawn_stage"])

    def test_never_a_shell(self):
        self.make_row("task-0001")
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            popen.return_value = unittest.mock.Mock(pid=1)
            supervisor.handle_run(row(updated=aged(99999)), supervisor.empty_registry(),
                                  cfg(), time.time(), act=True)
        self.assertIs(False, popen.call_args[1]["shell"])

    def test_the_spawn_command_is_configurable(self):
        argv = supervisor.spawn_argv(cfg(spawn_cmd=["mytool", "--task", "{task}", "{stage}"]),
                                     "task-0042", "review", "why")
        self.assertEqual(["mytool", "--task", "task-0042", "review"], argv)

    def test_a_task_whose_file_left_active_is_not_relaunched(self):
        for where in ("done", "backlog", None):
            with self.subTest(folder=where):
                with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                        unittest.mock.patch.object(state, "task_dir", return_value=where):
                    event = supervisor.handle_run(row(updated=aged(99999)),
                                                  supervisor.empty_registry(), cfg(),
                                                  time.time(), act=True)
                popen.assert_not_called()
                self.assertIn("skipped", event["action"])

    def test_a_non_acting_pass_takes_no_action(self):
        # The --status path: it reports the action it would have taken and takes
        # none. (This was `test_dry_run_takes_no_action` until --dry-run was
        # deleted - it never had its own behaviour, only its own flag.)
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            event = supervisor.handle_run(row(updated=aged(99999)), reg, cfg(),
                                          time.time(), act=False)
        popen.assert_not_called()
        self.assertIn("would", event["action"])
        self.assertEqual(state.ST_IN_PROGRESS, self.read_row()["stage_status"])
        self.assertEqual([], reg["actions"])


class CapsTest(Sandbox):
    """Two hard caps. Exceeding either STOPS the supervisor."""

    def test_per_task_cap(self):
        reg = supervisor.empty_registry()
        conf = cfg(max_actions_per_task=3)
        for done in range(3):
            supervisor.entry_for(reg, "task-0001")["actions"] = done
            with self.subTest(actions=done):
                self.assertEqual("", supervisor.caps_exceeded(reg, "task-0001", conf,
                                                              time.time()))
        supervisor.entry_for(reg, "task-0001")["actions"] = 3
        reason = supervisor.caps_exceeded(reg, "task-0001", conf, time.time())
        self.assertIn("per-task action cap", reason)
        # Per TASK, so another task is unaffected by this one's budget.
        self.assertEqual("", supervisor.caps_exceeded(reg, "task-0002", conf, time.time()))

    def test_global_hourly_cap(self):
        now_ts = time.time()
        conf = cfg(max_actions_per_hour=2)
        reg = supervisor.empty_registry()
        reg["actions"] = [{"at": now_ts - 10, "task": "task-0001", "kind": "relaunch"},
                          {"at": now_ts - 20, "task": "task-0002", "kind": "relaunch"}]
        reason = supervisor.caps_exceeded(reg, "task-0003", conf, now_ts)
        self.assertIn("hourly action cap", reason)

    def test_the_hourly_window_rolls(self):
        now_ts = time.time()
        conf = cfg(max_actions_per_hour=2)
        reg = supervisor.empty_registry()
        reg["actions"] = [{"at": now_ts - 3601, "task": "task-0001", "kind": "relaunch"},
                          {"at": now_ts - 7200, "task": "task-0002", "kind": "relaunch"}]
        self.assertEqual("", supervisor.caps_exceeded(reg, "task-0003", conf, now_ts))

    def test_a_cap_stops_the_pass_instead_of_skipping_the_action(self):
        runs = [row("task-0001", updated=aged(99999)), row("task-0002", updated=aged(99999))]
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001")["actions"] = 99
        with unittest.mock.patch.object(supervisor, "load_registry", return_value=reg), \
                unittest.mock.patch.object(state, "all_runs", return_value=runs), \
                unittest.mock.patch.object(state, "task_dir", return_value="active"), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            result = supervisor.poll_once(cfg(), act=True)
        popen.assert_not_called()
        self.assertIn("per-task action cap", result["stop"])
        # Stopped, so the SECOND run was never even looked at. That is the point:
        # it does not carry on past its budget.
        self.assertEqual(1, len(result["events"]))

    def test_main_exits_three_when_a_cap_stops_it(self):
        # conveyor_runs is patched rather than read: a test that reads the
        # ambient mode file passes or fails by what the session happens to be
        # set to, which has bitten this suite before.
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs", return_value=True), \
                unittest.mock.patch.object(supervisor, "poll_once",
                                           return_value={"events": [], "stop": "cap reached"}), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", "--once"]):
            self.assertEqual(3, supervisor.main())
        self.assertTrue(any("STOPPING" in n for n in self.notices))

    def test_actions_are_recorded_against_both_caps(self):
        reg = supervisor.empty_registry()
        now_ts = time.time()
        supervisor.record_action(reg, "task-0001", supervisor.ACTION_RELAUNCH, now_ts)
        self.assertEqual(1, reg["tasks"]["task-0001"]["actions"])
        self.assertEqual(1, len(reg["actions"]))
        self.assertEqual(now_ts, reg["actions"][0]["at"])


class NeverPushMergeOrApproveTest(Sandbox):
    """The supervisor's hardest limit, checked three ways."""

    FORBIDDEN = ("approve.py", "git push", "git merge", "push_approved",
                 "commit_approved", "--gate", "move_task", "move_task_to_done")

    def test_the_source_contains_no_path_to_any_of_them(self):
        src = (PIPELINE_DIR / "supervisor.py").read_text(encoding="utf-8")
        for token in self.FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token, src)

    def test_the_only_run_store_write_is_the_blocked_status(self):
        # Calls only: `set_fields(` with the parenthesis, so a docstring that
        # NAMES the function (stage_age explains why `updated` is a row-touch
        # time, and cannot do that without saying set_fields) is not counted as
        # a write. Every real call still matches, which is what this pins.
        src = (PIPELINE_DIR / "supervisor.py").read_text(encoding="utf-8")
        writes = [line.strip() for line in src.splitlines() if "set_fields(" in line
                  and not line.strip().startswith("#")]
        self.assertEqual(1, len(writes), writes)
        self.assertIn("stage_status=state.ST_BLOCKED", writes[0])

    # The three steps a spawned session must never take, as commands a session
    # would really run. Named here so both tests below drive the same list.
    UNATTENDED_COMMANDS = (
        ("merge", "git merge feature/task-0001"),
        ("push", "git push -u origin feature/task-0001"),
        ("approval",
         "python .claude/tools/pipeline/approve.py --task task-0001 --gate commit"),
    )

    def test_the_spawn_carries_the_unattended_mark(self):
        # What relaunch() starts is a new MAIN session, which no agent profile
        # governs, so the refusal has to travel with the child. This reads the
        # real Popen call rather than the prompt text.
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            popen.return_value = unittest.mock.Mock(pid=99)
            spawned, _argv, _pid = supervisor.relaunch(
                supervisor.STALLED, "task-0001", "implement", "stalled", cfg())
        self.assertTrue(spawned)
        env = popen.call_args.kwargs["env"]
        self.assertEqual("1", env.get(supervisor.UNATTENDED_ENV))
        # The gate reads its own copy of the name. A rename on one side alone
        # would unmark every spawn while both files still looked right.
        self.assertEqual(supervisor.UNATTENDED_ENV, pretool_gate.UNATTENDED_ENV)

    def test_the_spawned_environment_is_denied_all_three_steps(self):
        # THE CRITERION, tested on what is actually spawned. The version this
        # replaced asserted that approvals.py never grants PUSH - true, and a
        # property of a neighbouring module: it constructed no spawned session,
        # so it was not evidence about one. Here the marked environment is built
        # and the REAL gate is driven, end to end, for each of the three.
        with unittest.mock.patch.dict(os.environ, {supervisor.UNATTENDED_ENV: "1"}):
            for step, command in self.UNATTENDED_COMMANDS:
                err = io.StringIO()
                with self.subTest(step=step), \
                        unittest.mock.patch.object(sys, "stderr", err):
                    code = pretool_gate.handle_bash(command, cwd=str(self.tmp))
                self.assertEqual(2, code, f"{step} was allowed: {command}")
                self.assertIn("UNATTENDED", err.getvalue())

    def test_without_the_mark_this_gate_allows_the_same_three(self):
        # The control, so the test above measures the mark rather than some
        # other gate that would have refused anyway.
        env = {k: v for k, v in os.environ.items() if k != supervisor.UNATTENDED_ENV}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            for step, command in self.UNATTENDED_COMMANDS:
                with self.subTest(step=step):
                    self.assertEqual(0, pretool_gate.check_unattended(command))

    def test_the_read_only_base_check_is_not_read_as_a_merge(self):
        # `git merge-base --is-ancestor` is the branch-base check every task
        # runs. A substring match on "git merge" would refuse it inside a
        # spawned session and leave the session unable to verify its own base.
        with unittest.mock.patch.dict(os.environ, {supervisor.UNATTENDED_ENV: "1"}):
            self.assertEqual(0, pretool_gate.check_unattended(
                "git merge-base --is-ancestor origin/main HEAD"))

    def test_a_real_relaunch_pass_leaves_every_approval_flag_at_zero(self):
        before = self.make_row("task-0001")
        reg = supervisor.empty_registry()
        with unittest.mock.patch.object(supervisor, "load_registry", return_value=reg), \
                unittest.mock.patch.object(state, "all_runs",
                                           return_value=[row(updated=aged(99999))]), \
                unittest.mock.patch.object(state, "task_dir", return_value="active"), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            popen.return_value = unittest.mock.Mock(pid=99)
            result = supervisor.poll_once(cfg(), act=True)
        self.assertEqual(supervisor.ACTION_RELAUNCH, result["events"][0]["action"])
        after = self.read_row()
        self.assertEqual(0, after["commit_approved"])
        self.assertEqual(0, after["push_approved"])
        self.assertEqual(before["stage"], after["stage"])
        self.assertEqual(before["stage_status"], after["stage_status"])
        self.assertEqual("", after["awaiting_human"])


class FailSafeTest(Sandbox):
    """The opposite direction from every hook in this tree: an error here must
    change nothing, and say so."""

    def test_a_classification_error_touches_no_pipeline_state(self):
        before = self.make_row("task-0001")
        reg = supervisor.empty_registry()
        with unittest.mock.patch.object(supervisor, "load_registry", return_value=reg), \
                unittest.mock.patch.object(state, "all_runs", return_value=[row()]), \
                unittest.mock.patch.object(supervisor, "classify",
                                           side_effect=RuntimeError("boom")), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            result = supervisor.poll_once(cfg(), act=True)
        popen.assert_not_called()
        self.assertEqual(before, self.read_row())
        self.assertIn("supervisor error", result["events"][0]["why"])
        self.assertIn("untouched", result["events"][0]["action"])
        self.assertTrue(any("untouched" in n for n in self.notices))

    def test_an_unreadable_run_store_stops_the_pass_and_classifies_nothing(self):
        with unittest.mock.patch.object(state, "connect", side_effect=OSError("locked")):
            result = supervisor.poll_once(cfg(), act=True)
        self.assertEqual([], result["events"])
        self.assertIn("could not read the run store", result["stop"])

    def test_a_failing_pass_exits_without_a_second_one(self):
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs", return_value=True), \
                unittest.mock.patch.object(supervisor, "poll_once",
                                           side_effect=RuntimeError("boom")), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py"]):
            self.assertEqual(2, supervisor.main())
        self.assertTrue(any("untouched" in n for n in self.notices))

    def test_parking_a_task_with_no_run_row_writes_nothing(self):
        self.assertFalse(supervisor.park_blocked("task-9999", "no such run"))

    def test_a_registry_entry_that_is_not_a_dict_is_dropped(self):
        # The container was validated but not the entries, and observe() calls
        # .get() on an entry: a string or a list in there raised, and the run was
        # reported as a supervisor error instead of being classified at all.
        supervisor.registry_path().write_text(json.dumps(
            {"tasks": {"task-0001": "not an entry", "task-0002": ["nor this"],
                       "task-0003": {"stage": "implement"}}, "actions": []}),
            encoding="utf-8")
        tasks = supervisor.load_registry()["tasks"]
        self.assertEqual(["task-0003"], list(tasks))
        # And a poll over such a registry classifies the run rather than erroring.
        self.make_row("task-0001")
        event = supervisor.poll_once(cfg(), act=False)["events"][0]
        self.assertIn(event["classification"], supervisor.CLASSIFICATIONS)
        self.assertNotIn("supervisor error", event["why"])

    def test_a_corrupt_registry_is_read_as_empty(self):
        supervisor.registry_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(supervisor.empty_registry(), supervisor.load_registry())
        supervisor.registry_path().write_text('["a list"]', encoding="utf-8")
        self.assertEqual(supervisor.empty_registry(), supervisor.load_registry())

    def test_saving_the_registry_never_raises(self):
        with unittest.mock.patch.object(state, "STATE_DIR", self.tmp / "nope" / "deeper"):
            self.assertTrue(supervisor.save_registry(supervisor.empty_registry()))
        with unittest.mock.patch.object(supervisor, "registry_path",
                                        return_value=self.tmp):  # a directory
            self.assertFalse(supervisor.save_registry(supervisor.empty_registry()))

    def test_the_registry_round_trips(self):
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001").update({"pid": 5, "loop_ticks": 2})
        supervisor.record_action(reg, "task-0001", supervisor.ACTION_PARK, time.time())
        self.assertTrue(supervisor.save_registry(reg))
        self.assertEqual(reg, supervisor.load_registry())

    def test_the_action_log_stays_bounded(self):
        reg = supervisor.empty_registry()
        for _ in range(600):
            supervisor.record_action(reg, "task-0001", supervisor.ACTION_PARK, time.time())
        supervisor.save_registry(reg)
        self.assertEqual(500, len(supervisor.load_registry()["actions"]))

    def test_an_unparseable_timestamp_yields_no_age_rather_than_a_stall(self):
        self.assertIsNone(supervisor.parse_updated("yesterday"))
        self.assertIsNone(supervisor.idle_seconds(row(updated="yesterday"), time.time()))
        label, _ = supervisor.classify(row(updated="yesterday"), {}, cfg(), time.time())
        self.assertEqual(supervisor.HEALTHY, label)

    def test_talk_mode_supervises_nothing(self):
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs", return_value=False), \
                unittest.mock.patch.object(supervisor.mode, "read", return_value="talk"), \
                unittest.mock.patch.object(supervisor, "poll_once") as poll, \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", "--once"]):
            self.assertEqual(0, supervisor.main())
        poll.assert_not_called()

    def test_disabled_in_config_supervises_nothing(self):
        with unittest.mock.patch.object(supervisor, "config", return_value=cfg(enabled=False)), \
                unittest.mock.patch.object(supervisor, "poll_once") as poll, \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", "--once"]):
            self.assertEqual(0, supervisor.main())
        poll.assert_not_called()


class ConfigAndLaneTest(Sandbox):
    """The shipped config must actually drive the code, and one supervisor must
    stay inside one lane."""

    def test_the_shipped_pipeline_json_configures_every_default(self):
        block = json.loads((state.ROOT / ".agentry" / "pipeline.json").read_text(
            encoding="utf-8")).get("supervisor")
        self.assertIsNotNone(block, "pipeline.json has no supervisor block")
        for key in supervisor.DEFAULTS:
            with self.subTest(key=key):
                self.assertIn(key, block)

    def test_config_reads_the_block_and_the_continuation_ceiling(self):
        fake = {"continuation_ceiling": 7, "supervisor": {"poll_seconds": 5,
                                                          "max_actions_per_task": 1}}
        with unittest.mock.patch.object(state, "load_pipeline", return_value=fake):
            conf = supervisor.config()
        self.assertEqual(7, conf["continuation_ceiling"])
        self.assertEqual(5, conf["poll_seconds"])
        self.assertEqual(1, conf["max_actions_per_task"])
        # Unset keys keep their default rather than vanishing.
        self.assertEqual(supervisor.DEFAULTS["stall_seconds"], conf["stall_seconds"])

    def test_an_unreadable_config_yields_the_defaults(self):
        with unittest.mock.patch.object(state, "load_pipeline", side_effect=OSError):
            conf = supervisor.config()
        for key, value in supervisor.DEFAULTS.items():
            with self.subTest(key=key):
                self.assertEqual(value, conf[key])

    def test_every_supervisor_file_is_suffixed_by_the_lane(self):
        for lane, suffix in (("", ""), ("planning", ".planning")):
            with self.subTest(lane=lane or "default"):
                with unittest.mock.patch.object(state, "LANE_SUFFIX", suffix):
                    self.assertEqual(f"supervisor{suffix}.json",
                                     supervisor.registry_path().name)
                    self.assertEqual(f"supervisor{suffix}.log", supervisor.log_path().name)
                    self.assertEqual(f"supervisor-runs{suffix}", supervisor.run_log_dir().name)

    def test_the_lane_is_resolved_only_through_state(self):
        # state.py is the single reader of the PIPELINE_LANE environment
        # variable, and it validates the value. Reading the environment here
        # would be a second, unvalidated answer to which lane we are in.
        #
        # The probes are the LOOKUP forms rather than the bare name `os.environ`,
        # which this test used to ban outright. relaunch() has to hand the
        # spawned session an environment to inherit - passing `env=` replaces the
        # child's environment wholesale, so without the copy the child would lose
        # PATH, and PIPELINE_LANE with it, and the spawn would land in the wrong
        # lane or not run at all. A bulk copy resolves nothing; a lookup does.
        # The count assertion below keeps the copy from becoming cover for one.
        src = (PIPELINE_DIR / "supervisor.py").read_text(encoding="utf-8")
        for probe in ("os.environ.get", "os.environ[", "os.environ.copy", "getenv"):
            with self.subTest(probe=probe):
                self.assertNotIn(probe, src, f"{probe} in supervisor.py: the lane must come "
                                             f"from state.py, not from the environment")
        mentions = [line.strip() for line in src.splitlines()
                    if "os.environ" in line and not line.strip().startswith("#")]
        self.assertEqual(1, len(mentions), f"os.environ appears {len(mentions)} times: "
                                           f"{mentions}. The only legitimate use is the "
                                           f"inherited environment of the spawned session.")
        self.assertIn("env={**os.environ", mentions[0])
        self.assertIn(supervisor.UNATTENDED_ENV, src)
        self.assertIn("state.LANE_SUFFIX", src)


class CheckOrderTest(Sandbox):
    """The order of the four checks, which is load-bearing rather than cosmetic.

    classify() runs LOOPING, then liveness (DEAD), then the clock (STALLED).
    Swapping any pair is a realistic future edit, so each pair gets a run that
    satisfies BOTH conditions and asserts which label wins."""

    def test_looping_wins_when_a_run_matches_all_three_conditions_at_once(self):
        # Loop evidence, a dead owner, and an ancient row. Only the LOOPING
        # branch being first keeps this out of the relaunch path: DEAD and
        # STALLED are both in RELAUNCHABLE.
        proc = spawn_child()
        proc.terminate()
        proc.wait(timeout=30)
        entry = {"loop_ticks": 4, "stage": "implement", "continuations": 11,
                 "pid": proc.pid, "spawn_stage": "implement"}
        label, why = supervisor.classify(row(continuations=11, updated=aged(99999)),
                                         entry, cfg(), time.time())
        self.assertEqual(supervisor.LOOPING, label, why)
        self.assertIn(label, supervisor.CLASSIFICATIONS)
        self.assertNotIn(label, supervisor.RELAUNCHABLE)

    def test_dead_wins_over_stalled(self):
        # Liveness is observed fact, the clock is an inference, so an owner
        # known to have exited must be reported as DEAD even when the row is
        # also old enough to be a stall. Both are relaunchable, so what this
        # protects is the accuracy of the reason a human reads, not the action.
        proc = spawn_child()
        proc.terminate()
        proc.wait(timeout=30)
        entry = {"pid": proc.pid, "spawn_stage": "implement", "stage": "implement",
                 "continuations": 0}
        label, why = supervisor.classify(row(updated=aged(99999)), entry,
                                         cfg(stall_seconds=1200), time.time())
        self.assertEqual(supervisor.DEAD, label, why)
        self.assertIn("exited", why)

    def test_a_live_spawned_session_keeps_an_ancient_row_healthy(self):
        # The stall clock must not fire underneath a session that is demonstrably
        # still working. Without liveness ahead of the clock, a spawned session
        # running a long gate gets a second session spawned on top of it.
        proc = spawn_child()
        self.addCleanup(proc.wait, 30)
        self.addCleanup(proc.kill)
        entry = {"pid": proc.pid, "spawn_stage": "implement", "stage": "implement",
                 "continuations": 0}
        label, why = supervisor.classify(row(updated=aged(99999)), entry,
                                         cfg(stall_seconds=1200), time.time())
        self.assertEqual(supervisor.HEALTHY, label, why)
        self.assertIn("alive", why)


class StallClockSemanticsTest(Sandbox):
    """What the stall clock actually measures.

    MEASURED DEFECT, task-0058, recorded here rather than smoothed over. The
    detector read the run row's `updated` column, and `state.set_fields` stamps
    that column on EVERY write, so it is a row-touch time and not a
    stage-change time. `stop_gate.py` bumps `continuations` on Stop events and
    `advance.py` writes `stage_status` / `retries` / `awaiting_human`, all
    without the stage moving - each of those reset the only clock the detector
    had, and the HEALTHY reason string then claimed the stage changed when it did
    not. Observed live on task-0010: `continuations` 6, `updated` refreshed to
    the last Stop event, `why` reading "stage 'implement' changed 44s ago" after
    roughly forty minutes at one stage. So the Stop hook nagging about a task was
    what hid that task from the supervisor.

    FIXED by measuring time since the STAGE changed, kept in the supervisor's own
    registry (`stage_since`, written by observe() and only on an observed stage
    change). Nothing that writes run.db can reach it. These tests hold the new
    semantics; the decorator that marked the first one as an expected failure was
    removed with the fix, deliberately, so that a regression fails the run."""

    def poll_series(self, conf, touches, continuations=0, interval=300.0):
        """Simulate consecutive polls of one run whose stage never changes.

        The poll time is passed in rather than slept through (both observe and
        classify take it), so `interval` is the simulated gap between polls and
        each poll sees the row touched `age` seconds before it - the way any
        bookkeeping write touches it. `continuations` is held still so that no
        loop evidence accumulates: this isolates the stall clock from the LOOPING
        branch, which would otherwise catch the continuations case for a
        different reason."""
        entry = {}
        base = time.time()
        label, why = supervisor.HEALTHY, ""
        for i, age in enumerate(touches):
            now_ts = base + i * interval
            r = row(continuations=continuations, updated=stamp(now_ts - age))
            supervisor.observe(entry, r, now_ts)
            label, why = supervisor.classify(r, entry, conf, now_ts)
        return label, why, entry

    def test_a_stage_unchanged_for_stall_seconds_is_stalled_even_when_the_row_is_touched(self):
        conf = cfg(stall_seconds=1200, loop_ticks=2)
        # Twelve polls at five-minute intervals: an hour at one stage, with the
        # row touched a minute before every poll and nothing else changing.
        label, why, _ = self.poll_series(conf, [60] * 12)
        self.assertEqual(supervisor.STALLED, label,
                         f"an hour at one stage read as {label}: {why}")
        # The row-touch age the old detector used is still a minute, four orders
        # of magnitude below the threshold, which is exactly why it could not be
        # the clock.
        self.assertIn("unchanged for", why)

    def test_a_stage_change_is_the_only_thing_that_resets_the_clock(self):
        conf = cfg(stall_seconds=1200, loop_ticks=2)
        entry = {}
        base = time.time()
        # An hour at 'implement', then the stage moves: the clock restarts from
        # the observed transition, so the next poll is healthy again.
        for i in range(12):
            now_ts = base + i * 300
            r = row(updated=stamp(now_ts - 60))
            supervisor.observe(entry, r, now_ts)
            label, _ = supervisor.classify(r, entry, conf, now_ts)
        self.assertEqual(supervisor.STALLED, label)

        moved_at = base + 3600
        r = row(stage="test", updated=stamp(moved_at))
        supervisor.observe(entry, r, moved_at)
        label, why = supervisor.classify(r, entry, conf, moved_at)
        self.assertEqual(supervisor.HEALTHY, label, why)
        self.assertIn("observed", why)
        self.assertEqual(moved_at, entry["stage_since"])

    def test_the_healthy_reason_does_not_claim_a_stage_change_it_did_not_observe(self):
        # The log line is part of the defect: "stage 'implement' changed 44s ago"
        # was printed while measuring a row touch, and a line that lies during an
        # incident is worse than no line. First sight can only give a lower
        # bound, and it has to say so.
        entry = {}
        now_ts = time.time()
        r = row(updated=stamp(now_ts - 60))
        supervisor.observe(entry, r, now_ts)
        _, why = supervisor.classify(r, entry, cfg(), now_ts)
        self.assertFalse(entry["stage_since_observed"])
        self.assertIn("at least", why)
        self.assertIn("seeded", why)
        self.assertNotIn("changed 60s ago", why)

    def test_a_real_bookkeeping_write_no_longer_hides_a_stalled_run(self):
        # The defect end to end, through handle_run and a REAL run row: a write
        # of `retries` is what advance.py does on a failed gate, and `updated`
        # is stamped by it every time. Nothing here fakes the reset.
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        conf = cfg(stall_seconds=1200)
        base = time.time()
        label = None
        for i in range(13):
            now_ts = base + i * 300
            with unittest.mock.patch.object(state, "now", return_value=stamp(now_ts - 60)):
                conn = state.connect()
                try:
                    state.set_fields(conn, "task-0001", retries=i)
                finally:
                    conn.close()
            run = self.read_row()
            label = supervisor.handle_run(run, reg, conf, now_ts, act=False)["classification"]
        self.assertEqual(supervisor.STALLED, label)
        # And the row really was being touched throughout: the row-touch age at
        # the last poll is a minute, not an hour.
        self.assertAlmostEqual(60, supervisor.idle_seconds(self.read_row(), base + 3600),
                               delta=2)

    def test_a_climbing_continuations_never_moves_the_stall_clock(self):
        """The one field the rest of this suite holds still.

        poll_series() pins `continuations` on purpose, to keep the LOOPING
        branch from answering a question about the stall branch - so the field
        that CAUSED the original defect (stop_gate.py bumping `continuations` on
        every Stop nag, task-0010) was never pinned against the clock.
        Measured gap: adding `entry["stage_since"] = now_ts` to observe()'s
        continuations branch left this whole suite green.

        `loop_ticks` is set out of reach rather than the climb being slowed,
        because the question here is only whether a continuations bump moves
        stage_since. The LOOPING label for this series is correct behaviour and
        is covered by LoopDetectionEndToEndTest.

        The LABEL assertion moved from STALLED to LOOPING in the fourth round,
        when classify()'s stall branch started consulting `stage_climbs`: an hour
        of climbing continuations at one stage is a slow loop by the new
        boundary, and calling it STALLED is what got such a run relaunched (see
        SlowLoopBoundaryTest). Nothing was weakened - this test's own guard is
        the stage_since pair below, which fails if observe() ever moves the clock
        from the continuations branch, whatever the label says.
        """
        conf = cfg(stall_seconds=1200, loop_ticks=10**6)
        entry = {}
        base = time.time()
        seeded = None
        label, why = supervisor.HEALTHY, ""
        # Thirteen polls at five-minute intervals: an hour at one stage, with
        # `continuations` climbing on EVERY poll and the row touched a minute
        # before each one, exactly as a Stop-hook nag touches it.
        for i in range(13):
            now_ts = base + i * 300
            r = row(continuations=i, updated=stamp(now_ts - 60))
            supervisor.observe(entry, r, now_ts)
            label, why = supervisor.classify(r, entry, conf, now_ts)
            if i == 0:
                seeded = entry["stage_since"]
        self.assertEqual(supervisor.LOOPING, label,
                         f"an hour of climbing continuations at one stage read as {label}: {why}")
        # The clock never moved off the value seeded at first sight, an hour ago.
        self.assertEqual(seeded, entry["stage_since"])
        self.assertFalse(entry["stage_since_observed"])

    def test_a_lost_registry_re_seeds_conservatively_rather_than_claiming_a_stall(self):
        # The registry is the supervisor's own file and can be lost. First sight
        # of a task then has no observed transition, so it uses the row's last
        # write - a lower bound, which under-states the age and errs towards
        # HEALTHY. That is the fail-safe direction: a missed relaunch costs one
        # poll interval, a wrong one costs a duplicate session.
        entry = {}
        now_ts = time.time()
        r = row(updated=stamp(now_ts - 60))
        supervisor.observe(entry, r, now_ts)
        label, _ = supervisor.classify(r, entry, cfg(stall_seconds=1200), now_ts)
        self.assertEqual(supervisor.HEALTHY, label)
        # An ancient row on first sight is still caught, because the lower bound
        # is itself past the threshold.
        entry = {}
        r = row(updated=stamp(now_ts - 99999))
        supervisor.observe(entry, r, now_ts)
        label, _ = supervisor.classify(r, entry, cfg(stall_seconds=1200), now_ts)
        self.assertEqual(supervisor.STALLED, label)

    def test_the_clock_survives_the_registry_round_trip(self):
        # stage_since is only useful if it is remembered between polls, and polls
        # are separate processes' worth of apart: it goes through JSON.
        reg = supervisor.empty_registry()
        entry = supervisor.entry_for(reg, "task-0001")
        now_ts = time.time()
        supervisor.observe(entry, row(updated=stamp(now_ts - 10)), now_ts)
        supervisor.observe(entry, row(stage="test"), now_ts + 5)
        self.assertTrue(supervisor.save_registry(reg))
        back = supervisor.load_registry()["tasks"]["task-0001"]
        self.assertEqual(now_ts + 5, back["stage_since"])
        self.assertTrue(back["stage_since_observed"])

    def test_the_clock_holds_through_poll_once_and_a_registry_file_on_disk(self):
        """The defect's real shape: separate polls, a real run row, a real
        registry FILE, and a bookkeeping write before every poll.

        The other tests here keep one `entry` dict alive across polls, which is
        not how the daemon runs - each poll loads the registry from disk and
        saves it again. If persistence ever stops carrying `stage_since` (a
        rename, a save that silently fails), those tests still pass while the
        daemon degrades to the row-touch clock the fix removed. This is the one
        that notices."""
        self.make_row("task-0001")
        conf = cfg(stall_seconds=1200)
        base = time.time()
        label = None
        for i in range(13):
            poll_at = base + i * 300
            # A real set_fields write a minute before each poll, stamping
            # `updated` exactly as the Stop hook's continuations bump does.
            with unittest.mock.patch.object(state, "now", return_value=stamp(poll_at - 60)):
                conn = state.connect()
                try:
                    state.set_fields(conn, "task-0001", retries=i)
                finally:
                    conn.close()
            with unittest.mock.patch.object(supervisor.time, "time", return_value=poll_at):
                result = supervisor.poll_once(conf, act=False)
            label = result["events"][0]["classification"]
        self.assertEqual(supervisor.STALLED, label)
        # And the clock really did travel through the file rather than a dict.
        saved = json.loads(supervisor.registry_path().read_text(encoding="utf-8"))
        self.assertAlmostEqual(base, saved["tasks"]["task-0001"]["stage_since"], delta=120)
        self.assertFalse(saved["tasks"]["task-0001"]["stage_since_observed"])

    def test_every_in_flight_run_keeps_a_usable_clock_in_the_registry(self):
        """The invariant any future registry PRUNE has to preserve.

        MEASURED on the live registry: 16 task entries, 3607 bytes, 225 bytes
        each, and 13 of the 16 are runs at stage 'done' that can never change
        again - yet they are read, walked and rewritten every poll, and nothing
        ever reclaims them. Pruning them is the obvious fix and it is safe,
        because a done run's entry is not load-bearing (classify() answers
        HEALTHY for a done row from an EMPTY entry - see
        ClassificationTest.test_healthy_when_already_blocked_or_done).

        What is NOT safe is pruning by age or by size. Dropping the entry of a
        run that is still in flight loses its `stage_since`, and the next poll
        re-seeds it from the row's last write - which is the under-reporting
        that hid task-0010 for forty minutes. A prune that resets the clock is
        the same defect in a different coat. So this test asserts the only thing
        that must survive any such change: after a poll, every run that is still
        in flight has a numeric clock in the registry."""
        self.make_row("task-0001", stage="implement")
        self.make_row("task-0002", stage="test")
        self.make_row("task-0003", stage="done")
        with unittest.mock.patch.object(state, "task_dir", return_value="active"):
            supervisor.poll_once(cfg(stall_seconds=99999), act=False)
        tasks = supervisor.load_registry()["tasks"]
        for task in ("task-0001", "task-0002"):
            with self.subTest(in_flight=task):
                self.assertIn(task, tasks, "an in-flight run lost its registry entry, so its "
                                           "stall clock restarts from the row's last write")
                self.assertIsInstance(tasks[task]["stage_since"], float)

    def test_a_stage_since_from_the_future_does_not_switch_the_clock_off(self):
        """The registry is JSON on disk, so its numbers are input, not facts.

        A `stage_since` later than now makes `now - since` negative, and the
        clamp to zero then reports an age of 0s on EVERY poll: the run is HEALTHY
        for good, and the reason string says "the stage change this supervisor
        observed" about a number nothing observed. No corruption is needed to get
        there - a laptop resuming with a clock correction, or an NTP step
        backwards, writes exactly this.

        MEASURED with the clamp removed (stage_since_of returning the raw value):
        ('HEALTHY', "stage 'implement' unchanged for 0s (since the stage change
        this supervisor observed)") on a row aged 99999s. With the clamp, the same
        entry gives STALLED and a reason that admits the value was discarded."""
        entry = {"stage": "implement", "continuations": 0,
                 "stage_since": time.time() + 86400, "stage_since_observed": True}
        run = row(updated=aged(99999))
        label, why = supervisor.classify(run, entry, cfg(stall_seconds=1200), time.time())
        self.assertEqual(supervisor.STALLED, label, why)
        self.assertIn("discarded", why)
        self.assertNotIn("observed", why,
                         "a discarded value must not be reported as an observation")

    def test_an_unusable_clock_is_repaired_rather_than_kept(self):
        # Discarding it per poll would leave the bad number in the file forever.
        # observe() re-seeds it, so the next poll has a usable clock again.
        for bad in (time.time() + 86400, "yesterday", None, float("nan")):
            with self.subTest(stage_since=bad):
                entry = {"stage": "implement", "continuations": 0,
                         "stage_since": bad, "stage_since_observed": True}
                now_ts = time.time()
                supervisor.observe(entry, row(updated=stamp(now_ts - 30)), now_ts)
                self.assertIsNotNone(supervisor.stage_since_of(entry, now_ts))
                self.assertLessEqual(entry["stage_since"], now_ts)
                self.assertFalse(entry["stage_since_observed"])

    def test_a_future_clock_does_not_hide_an_old_run_through_a_whole_poll(self):
        # End to end, through poll_once and a real registry file: the repair has
        # to happen where the daemon actually reads the value.
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001").update(
            {"stage": "implement", "continuations": 0,
             "stage_since": time.time() + 86400, "stage_since_observed": True})
        supervisor.save_registry(reg)
        with unittest.mock.patch.object(state, "all_runs",
                                        return_value=[row(updated=aged(99999))]):
            result = supervisor.poll_once(cfg(stall_seconds=1200), act=False)
        self.assertEqual(supervisor.STALLED, result["events"][0]["classification"])
        saved = supervisor.load_registry()["tasks"]["task-0001"]
        self.assertLessEqual(saved["stage_since"], time.time())
        self.assertFalse(saved["stage_since_observed"])

    def test_the_row_touch_that_resets_the_clock_is_a_real_write_path(self):
        # Not a hypothetical: this is the write that was measured resetting the
        # clock on task-0010. It is here so the defect above cannot be dismissed
        # as a test-only construction - a continuations bump is a set_fields
        # call, and set_fields stamps `updated`.
        before = self.make_row("task-0001")
        time.sleep(1.1)
        conn = state.connect()
        try:
            state.set_fields(conn, "task-0001", continuations=1)
        finally:
            conn.close()
        after = self.read_row()
        self.assertEqual(before["stage"], after["stage"])
        self.assertNotEqual(before["updated"], after["updated"],
                            "set_fields must stamp `updated`, which is why it is a "
                            "row-touch time and not a stage-change time")


class RegistryPersistenceTest(Sandbox):
    """The registry IS the stall clock, so failing to write it is a detector
    outage and has to be reported as one. And what it keeps has to be pruned by
    liveness, never by age."""

    def test_a_registry_that_cannot_be_written_says_so_on_every_poll(self):
        self.make_row("task-0001")
        with unittest.mock.patch.object(supervisor, "save_registry", return_value=False):
            for _ in range(3):
                supervisor.poll_once(cfg(), act=False)
        loud = [n for n in self.notices if "COULD NOT WRITE" in n]
        self.assertEqual(3, len(loud), "a persistence failure must be reported every poll, "
                                       "not once and then swallowed")
        # It has to name the consequence, not just the error: without the
        # registry the clock silently reverts to the row-touch time this round
        # replaced, which is the failure a reader needs to recognise.
        self.assertIn("re-seeds it from the run row", loud[0])
        self.assertIn(str(supervisor.registry_path()), loud[0])

    def test_a_successful_poll_is_quiet(self):
        self.make_row("task-0001")
        supervisor.poll_once(cfg(), act=False)
        self.assertEqual([], [n for n in self.notices if "COULD NOT WRITE" in n])

    def test_done_runs_are_skipped_entirely(self):
        # The measured cost was three things at once: log volume, an ever-growing
        # registry, and the walk itself. One skip fixes all three.
        self.make_row("task-0001", stage="implement")
        self.make_row("task-0002", stage="done")
        self.make_row("task-0003", stage="done")
        result = supervisor.poll_once(cfg(), act=False)
        self.assertEqual(["task-0001"], [e["task"] for e in result["events"]])
        self.assertEqual(["task-0001"], list(supervisor.load_registry()["tasks"]))

    def test_a_run_that_finishes_gives_its_registry_entry_back(self):
        self.make_row("task-0001", stage="implement")
        supervisor.poll_once(cfg(), act=False)
        self.assertIn("task-0001", supervisor.load_registry()["tasks"])
        conn = state.connect()
        try:
            state.set_fields(conn, "task-0001", stage="done")
        finally:
            conn.close()
        supervisor.poll_once(cfg(), act=False)
        self.assertEqual({}, supervisor.load_registry()["tasks"])

    def test_the_prune_keeps_every_in_flight_clock(self):
        # The invariant the prune must not break: pruning an in-flight entry
        # re-seeds its clock from the run row, which is this task's own defect.
        reg = supervisor.empty_registry()
        for task in ("task-0001", "task-0002", "task-0003"):
            supervisor.entry_for(reg, task)["stage_since"] = 1.0
        runs = [row("task-0001", stage="implement"), row("task-0002", stage="done")]
        removed = supervisor.prune_registry(reg, runs)
        self.assertEqual(2, removed, "a done run and a run no longer in the store")
        self.assertEqual(["task-0001"], list(reg["tasks"]))
        self.assertEqual(1.0, reg["tasks"]["task-0001"]["stage_since"])

    def test_the_prune_never_looks_at_age(self):
        # An ancient entry for a run that is still in flight is the NORMAL state
        # of a stalled task, which is the one thing the supervisor exists to
        # catch. Pruning by age would delete exactly that.
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001").update({"stage_since": 0.0, "seen": 0.0})
        self.assertEqual(0, supervisor.prune_registry(
            reg, [row("task-0001", stage="implement")]))
        self.assertEqual(0.0, reg["tasks"]["task-0001"]["stage_since"])


class PidAliveBranchTest(Sandbox):
    """pid_alive has three outcomes, not two: alive, dead, and cannot-tell.

    Cannot-tell must report ALIVE, because for this process dead means relaunch
    and a wrong relaunch puts a second writer on the tree. The real-pid tests in
    DeadSessionTest cover alive and dead; these pin the mapping itself, with the
    kernel32 answer faked so the branch is exercised deterministically rather
    than depending on which pids this host happens to own."""

    def probe(self, err):
        """pid_alive with OpenProcess failing and GetLastError returning err."""
        k32 = unittest.mock.Mock()
        k32.OpenProcess.return_value = 0
        with unittest.mock.patch.object(ctypes, "WinDLL", return_value=k32), \
                unittest.mock.patch.object(ctypes, "get_last_error", return_value=err):
            return supervisor.pid_alive(4242)

    @unittest.skipUnless(os.name == "nt", "Windows error-code mapping")
    def test_only_error_invalid_parameter_means_dead(self):
        self.assertFalse(self.probe(supervisor._ERROR_INVALID_PARAMETER))

    @unittest.skipUnless(os.name == "nt", "Windows error-code mapping")
    def test_every_other_openprocess_failure_means_alive(self):
        # 5 is the measured access-denied case for a live process we do not own.
        # 1234 and 0 stand for any reason nobody has met yet: unknown is still
        # cannot-tell, and cannot-tell is alive. Simplifying this branch back to
        # `return False` fails all three.
        for err in (supervisor._ERROR_ACCESS_DENIED, 1234, 0):
            with self.subTest(last_error=err):
                self.assertTrue(self.probe(err),
                                "an OpenProcess failure that is not 87 must read alive")

    @unittest.skipUnless(os.name == "nt", "Windows probe fallback")
    def test_a_probe_that_cannot_run_at_all_reports_alive(self):
        with unittest.mock.patch.object(ctypes, "WinDLL", side_effect=OSError("no kernel32")):
            self.assertTrue(supervisor.pid_alive(4242))

    def test_the_posix_branch_maps_the_documented_signal_outcomes(self):
        # Dead code on this host and therefore the most likely thing to rot. The
        # access case (PermissionError) must read alive for the same reason the
        # Windows branch treats error 5 as alive.
        cases = ((ProcessLookupError("gone"), False), (PermissionError("not ours"), True),
                 (OSError("odd"), True), (None, True))
        for exc, expected in cases:
            with self.subTest(raises=type(exc).__name__ if exc else "nothing"):
                with unittest.mock.patch.object(supervisor.os, "name", "posix"), \
                        unittest.mock.patch.object(supervisor.os, "kill",
                                                   side_effect=exc) as kill:
                    self.assertEqual(expected, supervisor.pid_alive(4242))
                kill.assert_called_once_with(4242, 0)


class CapBoundaryTest(Sandbox):
    """Both caps at their edges: one below, exactly at, one over. A cap tested
    only in the middle would not notice an off-by-one that acts once too often."""

    def test_the_per_task_cap_at_its_edges(self):
        conf = cfg(max_actions_per_task=3)
        reg = supervisor.empty_registry()
        for done, allowed in ((2, True), (3, False), (4, False)):
            with self.subTest(actions_already_taken=done):
                supervisor.entry_for(reg, "task-0001")["actions"] = done
                reason = supervisor.caps_exceeded(reg, "task-0001", conf, time.time())
                self.assertEqual(allowed, reason == "", reason)

    def test_the_hourly_cap_at_its_edges(self):
        now_ts = time.time()
        conf = cfg(max_actions_per_hour=2)
        for count, allowed in ((1, True), (2, False), (3, False)):
            with self.subTest(actions_in_the_last_hour=count):
                reg = supervisor.empty_registry()
                reg["actions"] = [{"at": now_ts - 10, "task": "task-0001",
                                   "kind": "relaunch"} for _ in range(count)]
                reason = supervisor.caps_exceeded(reg, "task-0009", conf, now_ts)
                self.assertEqual(allowed, reason == "", reason)

    def test_the_hourly_window_edge_is_exactly_one_hour(self):
        now_ts = time.time()
        conf = cfg(max_actions_per_hour=1)
        for age, counted in ((3599, True), (3600, False), (3601, False)):
            with self.subTest(action_age_seconds=age):
                reg = supervisor.empty_registry()
                reg["actions"] = [{"at": now_ts - age, "task": "task-0001",
                                   "kind": "relaunch"}]
                reason = supervisor.caps_exceeded(reg, "task-0002", conf, now_ts)
                self.assertEqual(counted, reason != "", reason)

    def test_the_hourly_cap_also_stops_the_pass(self):
        # The per-task cap stopping the pass is covered above; the hourly one is
        # a different branch and must stop it too, not skip one task and go on.
        runs = [row("task-0001", updated=aged(99999)), row("task-0002", updated=aged(99999))]
        reg = supervisor.empty_registry()
        reg["actions"] = [{"at": time.time() - 5, "task": "task-0007", "kind": "relaunch"}]
        with unittest.mock.patch.object(supervisor, "load_registry", return_value=reg), \
                unittest.mock.patch.object(state, "all_runs", return_value=runs), \
                unittest.mock.patch.object(state, "task_dir", return_value="active"), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            result = supervisor.poll_once(cfg(max_actions_per_hour=1), act=True)
        popen.assert_not_called()
        self.assertIn("hourly action cap", result["stop"])
        self.assertEqual(1, len(result["events"]))

    def test_a_capped_looping_run_is_not_even_parked(self):
        # The cap is checked before the action, so a LOOPING run at its budget is
        # left exactly as it was rather than written to. Parking is the gentlest
        # thing the supervisor does, and it is still an action.
        before = self.make_row("task-0001", continuations=9)
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001").update(
            # continuations one below the row's: this poll observes a climb, so
            # the consecutive count carries rather than resetting.
            {"loop_ticks": 3, "stage": "implement", "continuations": 8, "actions": 3})
        event = supervisor.handle_run(row(continuations=9), reg, cfg(max_actions_per_task=3),
                                      time.time(), act=True)
        self.assertEqual(supervisor.LOOPING, event["classification"])
        self.assertIn("per-task action cap", event["stop"])
        self.assertEqual("", event["action"])
        self.assertEqual(before, self.read_row())


class SpawnCommandTest(Sandbox):
    """WHAT gets spawned, asserted exactly. A test that accepts any command
    would pass while the supervisor launched the wrong thing."""

    def relaunch_pass(self, conf=None, popen_effect=None):
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            if popen_effect is not None:
                popen.side_effect = popen_effect
            else:
                popen.return_value = unittest.mock.Mock(pid=4321)
            event = supervisor.handle_run(row(updated=aged(99999)), reg,
                                          conf or cfg(), time.time(), act=True)
        return event, popen, reg

    def test_the_spawned_argv_is_exactly_the_one_spawn_argv_builds(self):
        event, popen, _ = self.relaunch_pass()
        expected = supervisor.spawn_argv(cfg(), "task-0001", "implement", event["why"])
        self.assertEqual(expected, popen.call_args[0][0])
        self.assertEqual(expected, event["argv"])
        # Element by element, so a reordering or an extra flag cannot slip in.
        self.assertEqual(3, len(expected))
        self.assertEqual("claude", expected[0])
        self.assertEqual("-p", expected[1])
        # And the third element spelled out independently of spawn_argv, so this
        # cannot pass by comparing the code against itself.
        self.assertEqual(supervisor.RELAUNCH_PROMPT.format(
            task="task-0001", stage="implement", why=event["why"]), popen.call_args[0][0][2])

    def test_the_prompt_carries_the_task_the_stage_and_the_refusals(self):
        argv = supervisor.spawn_argv(cfg(), "task-0042", "review", "owner exited")
        prompt = argv[2]
        for fragment in ("task-0042", "review", "owner exited",
                         ".agentry/tasks/active/task-0042.md",
                         "advance.py --task task-0042"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, prompt)
        # The spawned session is told the three things the supervisor itself may
        # never do, so a relaunch cannot become a back door to them.
        for forbidden in ("Do not commit without", "merge a branch", "record any"):
            with self.subTest(instruction=forbidden):
                self.assertIn(forbidden, prompt)

    def test_the_spawn_is_detached_from_this_process_and_logged_to_a_file(self):
        _, popen, _ = self.relaunch_pass()
        kwargs = popen.call_args[1]
        self.assertIs(False, kwargs["shell"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertEqual(subprocess.STDOUT, kwargs["stderr"])
        self.assertEqual(str(state.ROOT), kwargs["cwd"])
        # stdout is a real open file in the lane's run-log directory, so the
        # spawned session's output survives the supervisor.
        self.assertEqual(str(supervisor.run_log_dir()),
                         str(Path(kwargs["stdout"].name).parent))
        self.assertTrue(Path(kwargs["stdout"].name).name.startswith("task-0001-"))

    def test_a_spawn_that_fails_changes_nothing_and_counts_nothing(self):
        event, popen, reg = self.relaunch_pass(popen_effect=OSError("no claude on PATH"))
        popen.assert_called_once()
        self.assertIn("refused or failed", event["action"])
        self.assertEqual([], reg["actions"], "a failed spawn must not spend the budget")
        self.assertNotIn("pid", reg["tasks"]["task-0001"])
        self.assertEqual(state.ST_IN_PROGRESS, self.read_row()["stage_status"])
        self.assertTrue(any("spawn FAILED" in n and "untouched" in n for n in self.notices))


class LaneIsolationTest(Sandbox):
    """One supervisor, one lane. Crossing lanes would mean acting on a conveyor
    it is not watching, with another supervisor possibly acting on it too."""

    def test_a_supervisor_sees_only_its_own_lanes_runs(self):
        for suffix, task in (("", "task-0001"), (".planning", "task-0002")):
            with unittest.mock.patch.object(state, "LANE_SUFFIX", suffix), \
                    unittest.mock.patch.object(state, "DB_PATH",
                                               self.tmp / f"run{suffix}.db"):
                self.make_row(task)
        seen = {}
        for lane, suffix in (("default", ""), ("planning", ".planning")):
            with self.subTest(lane=lane):
                with unittest.mock.patch.object(state, "LANE_SUFFIX", suffix), \
                        unittest.mock.patch.object(state, "DB_PATH",
                                                   self.tmp / f"run{suffix}.db"):
                    result = supervisor.poll_once(cfg(), act=False)
                seen[lane] = sorted(e["task"] for e in result["events"])
        self.assertEqual(["task-0001"], seen["default"])
        self.assertEqual(["task-0002"], seen["planning"])


class OutsideTheSessionTest(unittest.TestCase):
    """The first acceptance criterion: a process independent of any session.

    This test used to assert the supervisor was NOT wired as a hook, on the
    reasoning that a hook would put it back inside the session lifecycle it
    exists to observe from outside. The CEO then required it to start itself
    ("он должен быть встроенным инструментом и раниться при старте сессии"), and
    the reasoning survives the requirement with one change: it is the LAUNCH that
    happens on the session event, and what the launcher starts is detached. So
    the assertion is now about which of the two the hook runs - a `--ensure-running`
    launcher is correct, a hook that runs the poll loop in the session's own
    process is the mistake this test still exists to catch."""

    def hooks_json(self) -> str:
        settings = Path(state.ROOT) / ".claude" / "settings.json"
        return json.dumps(json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}))

    def test_the_supervisor_is_launched_by_sessionstart_and_only_as_a_launcher(self):
        settings = Path(state.ROOT) / ".claude" / "settings.json"
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})
        commands = [h.get("command", "") for group in hooks.get("SessionStart", [])
                    for h in group.get("hooks", [])]
        ours = [c for c in commands if "supervisor.py" in c]
        self.assertEqual(1, len(ours), f"exactly one supervisor launcher expected: {commands}")
        self.assertIn("--ensure-running", ours[0],
                      "the hook must LAUNCH the detached daemon, never run the poll loop "
                      "inside the session it is supposed to outlive")
        # Control: the file really does wire the hooks it is supposed to, so the
        # assertions here are not passing because the block is empty.
        self.assertIn("stop_gate.py", self.hooks_json())

    def test_no_other_hook_event_runs_the_supervisor(self):
        # A PreToolUse or Stop hook running the supervisor would be back inside
        # the session's lifecycle, which is the whole failure this task is about.
        settings = Path(state.ROOT) / ".claude" / "settings.json"
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})
        for event, groups in hooks.items():
            if event == "SessionStart":
                continue
            with self.subTest(event=event):
                self.assertNotIn("supervisor", json.dumps(groups))


class LanePathTest(unittest.TestCase):
    """Deliberately NOT a Sandbox subclass: Sandbox repoints state.DB_PATH at a
    temporary file, so a lane assertion made inside it reads the patch rather
    than the real resolution and passes under the default lane whatever the code
    does. Caught exactly that way while mutation-testing with PIPELINE_LANE set."""

    def test_the_supervisors_own_four_files_really_live_under_agentry_state(self):
        # DetachedStartTest asserts log_path().parent == state.STATE_DIR, and it
        # runs inside Sandbox, where STATE_DIR is repointed at a temporary
        # directory - so that assertion reads the patch and would hold for any
        # location at all. This one runs unpatched, which is the only way to
        # check the real answer: all four files sit in the gitignored
        # .agentry/state, so nothing the supervisor writes can reach git.
        real = state.ROOT / ".agentry" / "state"
        self.assertEqual(real, state.STATE_DIR)
        for path in (supervisor.log_path(), supervisor.registry_path(),
                     supervisor.lock_path(), supervisor.run_log_dir()):
            with self.subTest(path=path.name):
                self.assertEqual(real, path.parent)
        self.assertIn(".agentry/state/",
                      (state.ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_the_run_store_path_is_derived_from_the_lane(self):
        # state.py is the only place the lane is resolved, so this one equality
        # is the whole coupling between a lane and the store it reads.
        self.assertEqual(f"run{state.LANE_SUFFIX}.db", Path(state.DB_PATH).name)
        self.assertEqual(state.STATE_DIR / f"run{state.LANE_SUFFIX}.db", state.DB_PATH)


class PollLoopTest(Sandbox):
    """It is a process with its own clock, so the clock gets a test."""

    def run_main(self, argv, conf=None, poll_result=None, sleeper=None):
        result = poll_result or {"events": [], "stop": ""}
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs", return_value=True), \
                unittest.mock.patch.object(supervisor, "config", return_value=conf or cfg()), \
                unittest.mock.patch.object(supervisor, "poll_once",
                                           return_value=result) as poll, \
                unittest.mock.patch.object(supervisor.time, "sleep",
                                           side_effect=sleeper or KeyboardInterrupt), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", *argv]):
            code = supervisor.main()
        return code, poll

    def test_it_polls_again_after_sleeping_the_configured_interval(self):
        slept: list[float] = []

        def sleeper(seconds):
            slept.append(seconds)
            if len(slept) == 2:
                raise KeyboardInterrupt
        code, poll = self.run_main([], conf=cfg(poll_seconds=60), sleeper=sleeper)
        self.assertEqual(0, code)
        self.assertEqual([60.0, 60.0], slept)
        # Poll, sleep, poll, sleep (which interrupts): two passes separated by the
        # configured interval, which is what having its own clock means.
        self.assertEqual(2, poll.call_count, "it must keep polling, not poll once and idle")
        self.assertTrue(any("stopped by the operator" in n for n in self.notices))

    def test_the_interval_flag_overrides_the_configured_poll_seconds(self):
        slept: list[float] = []

        def sleeper(seconds):
            slept.append(seconds)
            raise KeyboardInterrupt
        code, _ = self.run_main(["--interval", "7"], conf=cfg(poll_seconds=60),
                                sleeper=sleeper)
        self.assertEqual(0, code)
        self.assertEqual([7.0], slept)

    def test_once_makes_exactly_one_acting_pass(self):
        code, poll = self.run_main(["--once"])
        self.assertEqual(0, code)
        poll.assert_called_once()
        self.assertIs(True, poll.call_args[1]["act"])

    def test_status_acts_on_nothing(self):
        code, poll = self.run_main(["--status"])
        self.assertEqual(0, code)
        poll.assert_called_once()
        self.assertIs(False, poll.call_args[1]["act"], "a looking pass must never act")

    def test_a_mode_switch_mid_flight_stops_the_daemon(self):
        # The mode is re-read every poll, beside the lock check, and for the same
        # reason: the daemon is DETACHED, so it outlives the session that started
        # it and a mode read once at startup is stale the moment the lane is
        # switched to 'talk' or 'plan'. Before this, such a daemon kept
        # relaunching build work for a lane that no longer runs any.
        supervisor.write_lock(os.getpid(), time.time())
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs",
                                        side_effect=[True, False]), \
                unittest.mock.patch.object(supervisor.mode, "read", return_value="talk"), \
                unittest.mock.patch.object(supervisor, "poll_once",
                                           return_value={"events": [], "stop": ""}) as poll, \
                unittest.mock.patch.object(supervisor.time, "sleep", return_value=None):
            code = supervisor.poll_loop(
                cfg(), unittest.mock.Mock(once=False, status=False), act=True, daemon=True)
        self.assertEqual(0, code)
        self.assertEqual(1, poll.call_count, "it must stop on the poll after the switch")
        self.assertTrue(any("nothing to supervise" in n for n in self.notices), self.notices)


class LoopDetectionEndToEndTest(Sandbox):
    """The detector through poll_once, with a real run row and a real registry
    file, because that is the wiring the other LOOPING tests hand-assemble: the
    tick has to survive being written to disk and read back next poll."""

    def poll(self, continuations):
        conn = state.connect()
        try:
            state.set_fields(conn, "task-0001", continuations=continuations)
        finally:
            conn.close()
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            result = supervisor.poll_once(cfg(loop_ticks=2), act=True)
            popen.assert_not_called()
        return result["events"][0]

    def test_climbing_continuations_park_the_run_on_the_third_poll(self):
        self.make_row("task-0001")
        self.assertEqual(supervisor.HEALTHY, self.poll(1)["classification"])
        self.assertEqual(supervisor.HEALTHY, self.poll(2)["classification"],
                         "one climb is not yet a loop")
        third = self.poll(3)
        self.assertEqual(supervisor.LOOPING, third["classification"])
        self.assertEqual(supervisor.ACTION_PARK, third["action"])
        self.assertEqual(state.ST_BLOCKED, self.read_row()["stage_status"])
        self.assertEqual("implement", self.read_row()["stage"])
        self.assertEqual(2, supervisor.load_registry()["tasks"]["task-0001"]["loop_ticks"])

        # And once parked it is left alone: a blocked run was surfaced to a human
        # on purpose, so the fourth poll spends no further budget on it.
        fourth = self.poll(4)
        self.assertEqual(supervisor.HEALTHY, fourth["classification"])
        self.assertIn("already parked", fourth["why"])
        self.assertEqual(1, supervisor.load_registry()["tasks"]["task-0001"]["actions"])


class SlowLoopBoundaryTest(Sandbox):
    """A frozen stage WITH growth behind it, against a frozen stage with nothing
    moving at all. Both look identical to the stall clock, and only one of them
    may be relaunched.

    MEASURED REGRESSION (task-0058, fourth round). Making loop_ticks consecutive
    - which is correct, and stays - routed the slow loop into the relaunch path.
    With the shipped defaults, a frozen stage and continuations climbing every
    other poll (a nag cycle of about 90s against a 60s poll):

        poll 16 cont= 9 ticks=1 -> HEALTHY  action=-
        poll 20 cont=11 ticks=1 -> STALLED  action=relaunch
        poll 23: STOPPED - per-task action cap reached: 3/3 on task-0001
        sessions spawned for this looping run: 3

    loop_ticks oscillated 0/1 and never reached 2; the ceiling, named in the code
    as the backstop, needs 60 polls to reach 30 and never got them, because
    STALLED fires at poll 20 and STALLED is in RELAUNCHABLE. Three sessions were
    spawned onto a loop - the one outcome the module docstring forbids."""

    def drive(self, climb_every: int, polls: int = 25):
        """Poll one run whose stage never changes, bumping `continuations` every
        `climb_every` polls (0 = never). The poll time is passed in rather than
        slept through. `updated` is held at the first poll's value: the row-touch
        column is not the stall clock (see stage_age), and pinning it keeps this
        about the loop evidence.

        Returns (popen, events, reg) - popen is the patched spawner, so its call
        count IS the number of sessions started."""
        self.make_row("task-0001")
        reg = supervisor.empty_registry()
        base = time.time()
        events = []
        with unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen, \
                unittest.mock.patch.object(state, "task_dir", return_value="active"):
            # A live pid (our own), so a relaunched run reads HEALTHY next poll
            # instead of DEAD: this measures spawns caused by the label under
            # test, not spawns caused by a fake pid being absent.
            popen.return_value = unittest.mock.Mock(pid=os.getpid())
            for i in range(polls):
                now_ts = base + i * 60
                cont = (i // climb_every) if climb_every else 0
                r = row(continuations=cont, updated=stamp(base))
                events.append(supervisor.handle_run(r, reg, cfg(), now_ts, act=True))
        return popen, events, reg

    def test_a_loop_climbing_on_alternate_polls_is_parked_and_never_relaunched(self):
        popen, events, reg = self.drive(climb_every=2)
        labels = [e["classification"] for e in events]
        actions = [e["action"] for e in events]

        # The assertion the regression trace failed: zero sessions spawned.
        popen.assert_not_called()
        self.assertNotIn(supervisor.ACTION_RELAUNCH, actions)
        self.assertNotIn(supervisor.STALLED, labels,
                         f"a run with growth behind a frozen stage read as STALLED: {labels}")

        # Poll 16, before the stall threshold: still HEALTHY. The consecutive
        # semantics are intact - this fix must not re-park a run two nags into a
        # stage, which is what the third round removed.
        self.assertEqual(supervisor.HEALTHY, labels[16], events[16]["why"])
        self.assertEqual(1, reg["tasks"]["task-0001"]["loop_ticks"],
                         "the consecutive count must still be oscillating, not accumulating")

        # Poll 20, where the trace relaunched: LOOPING, and parked.
        self.assertEqual(supervisor.LOOPING, labels[20], events[20]["why"])
        self.assertEqual(supervisor.ACTION_PARK, events[20]["action"])
        self.assertIn("slow loop", events[20]["why"])
        self.assertEqual(state.ST_BLOCKED, self.read_row()["stage_status"])
        self.assertEqual("implement", self.read_row()["stage"])

    def test_a_loop_whose_owning_session_has_exited_is_parked_not_relaunched(self):
        # THE FOURTH PATH ONTO A LOOP, found in the fifth round's review. DEAD
        # is in RELAUNCHABLE, and the pid branch used to answer before
        # `stage_climbs` was read - so a loop whose spawned session had exited
        # was relabelled DEAD and relaunched, up to the per-task cap.
        #
        # MEASURED entry, from the reviewer: {loop_ticks:1, stage_climbs:5,
        # pid:<dead>, spawn_stage=='implement'} gave ('DEAD', ...) with
        # `in RELAUNCHABLE == True`. No clock is involved here at all: the row is
        # young and stall_seconds is set out of reach, so the dead owner is the
        # ONLY relaunchable evidence and the climbs must still outrank it.
        proc = spawn_child()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        proc.terminate()
        proc.wait(timeout=30)

        conf = cfg(stall_seconds=99999)
        r = row(continuations=11)
        entry = {"pid": proc.pid, "spawn_stage": "implement", "stage": "implement",
                 "continuations": 11, "loop_ticks": 1, "stage_climbs": 5}
        label, why = supervisor.classify(r, entry, conf, time.time())
        self.assertEqual(supervisor.LOOPING, label, why)
        self.assertNotIn(label, supervisor.RELAUNCHABLE)
        self.assertIn("slow loop", why)
        self.assertIn("exited", why)

        # And with nothing having climbed inside the stage, the same dead owner
        # is still DEAD: the fix must not switch relaunching off.
        entry["stage_climbs"] = 0
        label, why = supervisor.classify(r, entry, conf, time.time())
        self.assertEqual(supervisor.DEAD, label, why)

    def test_a_stall_with_no_loop_evidence_is_still_relaunched(self):
        # The other side of the boundary, and the control that stops the fix
        # above from being "never relaunch anything": nothing moving at all is a
        # plain stall, which is what the relaunch exists for.
        popen, events, _ = self.drive(climb_every=0)
        labels = [e["classification"] for e in events]

        popen.assert_called_once()
        self.assertEqual(supervisor.STALLED, labels[20], events[20]["why"])
        self.assertEqual(supervisor.ACTION_RELAUNCH, events[20]["action"])
        self.assertIn("no continuations growth", events[20]["why"])
        self.assertNotIn(supervisor.LOOPING, labels)
        # Not parked: a stalled run is restarted, not surfaced to the CEO.
        self.assertEqual(state.ST_IN_PROGRESS, self.read_row()["stage_status"])


class FailSafeInjectionTest(Sandbox):
    """The fail-safe direction, injected at several points rather than one.

    Every hook in this tree fails OPEN; this process fails SAFE. A future reader
    who knows the hooks will read that as a mistake and 'fix' it, so each point
    where an exception can land inside a poll gets an assertion that the run row
    came out byte-identical."""

    POINTS = ("observe", "caps_exceeded", "record_action", "spawn_argv")

    def test_an_error_anywhere_in_the_poll_leaves_the_run_row_identical(self):
        for point in self.POINTS:
            with self.subTest(raises_in=point):
                before = self.make_row("task-0001")
                reg = supervisor.empty_registry()
                with unittest.mock.patch.object(supervisor, "load_registry",
                                                return_value=reg), \
                        unittest.mock.patch.object(state, "all_runs",
                                                   return_value=[row(updated=aged(99999))]), \
                        unittest.mock.patch.object(state, "task_dir", return_value="active"), \
                        unittest.mock.patch.object(supervisor, point,
                                                   side_effect=RuntimeError("boom")), \
                        unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
                    popen.return_value = unittest.mock.Mock(pid=1)
                    result = supervisor.poll_once(cfg(), act=True)
                self.assertEqual(before, self.read_row())
                self.assertIn("supervisor error", result["events"][0]["why"])
                self.assertIn("untouched", result["events"][0]["action"])
                self.assertTrue(any("untouched" in n for n in self.notices))
                conn = state.connect()
                try:
                    conn.execute("DELETE FROM runs WHERE task = 'task-0001'")
                    conn.commit()
                finally:
                    conn.close()
                self.notices.clear()

    def test_an_error_reading_the_task_folder_leaves_the_run_row_identical(self):
        before = self.make_row("task-0001")
        with unittest.mock.patch.object(supervisor, "load_registry",
                                        return_value=supervisor.empty_registry()), \
                unittest.mock.patch.object(state, "all_runs",
                                           return_value=[row(updated=aged(99999))]), \
                unittest.mock.patch.object(state, "task_dir",
                                           side_effect=OSError("unreadable")), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            result = supervisor.poll_once(cfg(), act=True)
        popen.assert_not_called()
        self.assertEqual(before, self.read_row())
        self.assertIn("untouched", result["events"][0]["action"])

    def test_a_park_that_fails_records_no_action_and_leaves_the_row(self):
        before = self.make_row("task-0001", continuations=9)
        reg = supervisor.empty_registry()
        supervisor.entry_for(reg, "task-0001").update(
            # continuations one below the row's: this poll observes a climb.
            {"loop_ticks": 3, "stage": "implement", "continuations": 8})
        with unittest.mock.patch.object(state, "set_fields",
                                        side_effect=OSError("database is locked")), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            event = supervisor.handle_run(row(continuations=9), reg, cfg(),
                                          time.time(), act=True)
        popen.assert_not_called()
        self.assertIn("park failed", event["action"])
        self.assertIn("untouched", event["action"])
        self.assertEqual(before, self.read_row())
        self.assertEqual([], reg["actions"])

    def test_the_push_checkpoint_refusal_is_specific_and_not_a_blanket_false(self):
        # Control for the NEVER_GRANTED test above: granted() does answer True
        # for something, at the very level that grants the most, so PUSH coming
        # back False is a refusal of the push rather than a broken helper.
        self.assertIn(approvals.PUSH, approvals.NEVER_GRANTED)
        self.assertNotIn(approvals.PUSH, approvals.GRANTS[approvals.AUTO])
        original = approvals.read
        self.addCleanup(setattr, approvals, "read", original)
        approvals.read = lambda: approvals.AUTO
        self.assertTrue(approvals.granted(approvals.COMMIT))
        self.assertFalse(approvals.granted(approvals.PUSH))


class SingletonLockTest(Sandbox):
    """One supervisor per lane, and ownership that a recycled pid cannot fake.

    Two sessions starting in a morning is the normal case, not the edge case, so
    the second one must find the first and do nothing. The lock therefore has to
    answer three questions apart: held by a live process, left behind by a dead
    one, and claimed by a launcher that is mid-spawn."""

    def test_two_sessions_starting_it_yield_exactly_one_process(self):
        # The second session is simulated by calling the launcher again, which is
        # literally what a second SessionStart does. The spawned pid is this
        # process's own, so it is genuinely alive when the second call looks.
        with unittest.mock.patch.object(supervisor, "spawn_detached",
                                        return_value=os.getpid()) as spawn:
            first, first_msg = supervisor.ensure_running()
            second, second_msg = supervisor.ensure_running()
        self.assertTrue(first, first_msg)
        self.assertFalse(second, second_msg)
        self.assertIn("already running", second_msg)
        spawn.assert_called_once()
        self.assertEqual(os.getpid(), supervisor.read_lock()["pid"])

    def test_a_lock_naming_a_dead_pid_is_not_a_live_owner(self):
        # The criterion in one test: ownership is not "the file names a pid".
        # A dead owner's leftover lock must not keep a lane locked forever.
        #
        # The probe is faked rather than using a real reaped child, deliberately:
        # a just-exited pid is recycled quickly on a busy machine, and the full
        # suite spawns enough processes to hit it (measured - this test failed
        # once under the planning lane when pid 580 came back as somebody else).
        # DeadSessionTest already proves pid_alive against real processes; what
        # is under test here is the LOCK's use of its answer.
        supervisor.write_lock(4242, time.time())
        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False):
            kind, pid, why = supervisor.owner(time.time())
            self.assertEqual(supervisor.FREE, kind)
            self.assertEqual(4242, pid)
            self.assertIn("gone", why)
            with unittest.mock.patch.object(supervisor, "spawn_detached",
                                            return_value=os.getpid()) as spawn:
                started, msg = supervisor.ensure_running()
        self.assertTrue(started, msg)
        spawn.assert_called_once()

    def test_a_live_pid_that_stopped_writing_its_heartbeat_is_not_an_owner(self):
        # The recycled-pid case the criterion names, and the only one a pid
        # probe cannot answer: the number IS alive, but it belongs to something
        # else now. The daemon rewrites its lock every poll, so a lock that has
        # not moved for HEARTBEAT_STALE is nobody's - whatever the pid says.
        supervisor.write_lock(os.getpid(), time.time() - supervisor.HEARTBEAT_STALE - 1)
        kind, pid, why = supervisor.owner(time.time())
        self.assertEqual(supervisor.FREE, kind)
        self.assertEqual(0, pid, "a stale lock must not hand its pid to anything")
        self.assertIn("not been refreshed", why)
        # Fresh again: the same pid, the same probe, a different verdict.
        supervisor.write_lock(os.getpid(), time.time())
        self.assertEqual(supervisor.HELD, supervisor.owner(time.time())[0])

    def test_a_stale_heartbeat_is_released_rather_than_signalled(self):
        # The consequence of getting the above wrong is not a missed watcher, it
        # is --stop killing an unrelated process that happens to hold the pid.
        supervisor.write_lock(os.getpid(), time.time() - supervisor.HEARTBEAT_STALE - 1)
        with unittest.mock.patch.object(supervisor.os, "kill") as kill:
            ok, msg = supervisor.stop()
        kill.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("no supervisor is running", msg)
        self.assertIn("stale lock was released", msg)
        self.assertFalse(supervisor.lock_path().exists())

    def test_stop_refuses_to_signal_a_pid_whose_heartbeat_is_not_recent(self):
        """The identity question, and the honest limit of this design.

        The heartbeat answers liveness-staleness, not identity: a pid is a
        recycled number and nothing in a file can prove which process owns it.
        What the heartbeat gives is evidence - only a polling daemon rewrites
        this file, so a refresh two minutes old is strong evidence that the pid
        is still that daemon. --stop therefore uses a much shorter window than
        the start decision does, because signalling the wrong pid kills somebody
        else's process (proven in review with an innocent sleep(60)) while
        declining to signal only costs a manual kill.

        Residual, stated rather than hidden: inside the signal window a recycled
        pid could still be signalled. Closing that needs a process identity the
        standard library will not give us without a second probe."""
        proc = spawn_child()
        self.addCleanup(proc.wait, 30)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        # Alive, and inside the START window (ten minutes) but outside the
        # SIGNAL window (two), which is the whole point of having both.
        stale = time.time() - supervisor.SIGNAL_STALE - 1
        supervisor.write_lock(proc.pid, stale)
        self.assertEqual(supervisor.HELD, supervisor.owner(time.time())[0],
                         "it is still recent enough that no second daemon should start")
        with unittest.mock.patch.object(supervisor.os, "kill") as kill:
            ok, msg = supervisor.stop()
        kill.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("not been refreshed", msg)
        self.assertIsNone(proc.poll(), "an innocent process must not be signalled")

    def test_the_two_windows_are_strict_and_tolerant_on_purpose(self):
        self.assertLess(supervisor.SIGNAL_STALE, supervisor.HEARTBEAT_STALE,
                        "the kill decision must be stricter than the start decision")
        self.assertEqual(supervisor.SIGNAL_STALE, supervisor.signal_window(cfg(poll_seconds=60)))
        self.assertEqual(600.0, supervisor.signal_window(cfg(poll_seconds=300)))

    def test_the_heartbeat_window_never_undercuts_the_poll_interval(self):
        self.assertEqual(supervisor.HEARTBEAT_STALE,
                         supervisor.heartbeat_window(cfg(poll_seconds=60)))
        self.assertEqual(3600.0, supervisor.heartbeat_window(cfg(poll_seconds=1200)))
        self.assertEqual(supervisor.HEARTBEAT_STALE,
                         supervisor.heartbeat_window(cfg(poll_seconds="nonsense")))

    def test_liveness_comes_from_pid_alive_and_nothing_else(self):
        # "Reuse pid_alive() rather than adding a second liveness probe": the
        # lock asks that one function, so the DEAD classification and the
        # singleton can never disagree about whether a process is running.
        supervisor.write_lock(4242, time.time())
        with unittest.mock.patch.object(supervisor, "pid_alive",
                                        return_value=True) as alive:
            self.assertEqual(supervisor.HELD, supervisor.owner(time.time())[0])
        alive.assert_called_with(4242)
        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False):
            self.assertEqual(supervisor.FREE, supervisor.owner(time.time())[0])

    def test_there_is_exactly_one_liveness_probe_in_the_file(self):
        # One probe, and everybody else asks it. Two probes would be two answers
        # to "is that process running", and the singleton and the DEAD
        # classification would eventually disagree about the same pid.
        src = (PIPELINE_DIR / "supervisor.py").read_text(encoding="utf-8")
        probe = src[src.index("def pid_alive"):src.index("def empty_registry")]
        outside = src.replace(probe, "")
        for call in ("k32.OpenProcess(", "k32.GetExitCodeProcess(", "os.kill(pid, 0)"):
            with self.subTest(call=call):
                self.assertIn(call, probe)
                self.assertNotIn(call, outside,
                                 f"{call} outside pid_alive is a second liveness probe")
        # Control: the rest of the file really does consult it, so the absence
        # above is reuse rather than nobody checking liveness at all.
        self.assertGreaterEqual(outside.count("pid_alive("), 3)

    def test_the_claim_is_exclusive_so_two_launchers_cannot_both_spawn(self):
        now_ts = time.time()
        self.assertTrue(supervisor.claim_lock(now_ts))
        # A second launcher reaching the same line loses: the file exists and its
        # claim is inside the grace window, so it is not stealable.
        self.assertFalse(supervisor.claim_lock(now_ts))
        kind, _, why = supervisor.owner(now_ts)
        self.assertEqual(supervisor.CLAIMING, kind)
        self.assertIn("starting one now", why)

    def test_concurrent_launchers_never_both_win_the_claim(self):
        """The claim must be ATOMIC, not a check followed by a create.

        The sequential test above cannot tell the two apart: a plain
        `if lock_path().exists()` passes it. Measured - with the exclusive
        create replaced by that check, four launchers released together from a
        barrier produced more than one winner in 53, 58 and 59 of 60 attempts;
        with os.O_EXCL it was 0 of 180. So this loop detects the non-atomic
        version with near-certainty and can never fail on the real one, because
        two successful exclusive creates of one path are impossible rather than
        unlikely."""
        for attempt in range(60):
            with self.subTest(attempt=attempt):
                try:
                    supervisor.lock_path().unlink()
                except OSError:
                    pass
                won: list[int] = []
                barrier = threading.Barrier(4)

                def racer():
                    barrier.wait()
                    if supervisor.claim_lock(time.time()):
                        won.append(1)

                threads = [threading.Thread(target=racer) for _ in range(4)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)
                self.assertEqual(1, len(won),
                                 "more than one launcher won the right to spawn, so more "
                                 "than one supervisor would have been started")

    def test_a_launcher_inside_anothers_steal_window_does_not_also_win(self):
        """The audit's third finding, pinned deterministically.

        With a stale lock present - the NORMAL state after a crash - the steal
        used to be `owner() says FREE` then `unlink`, and a launcher running
        inside that window also won: its unlink removed the winner's brand new
        lock, so both created one and both spawned. Threads do not reliably land
        in a window that narrow (measured: 60 races of 4 launchers each hit it
        zero times on this host, which is why the thread test below cannot be
        trusted to catch it), so launcher B is placed inside launcher A's window
        by hand.

        Two assertions, and the first is what makes this load-bearing: the steal
        MUST go through the rename, and exactly one launcher may win. MEASURED
        with the rename put back to unlink: 'the steal must go through an
        atomic rename' fails, because the interleave point is never reached."""
        supervisor.write_lock(4242, time.time())  # a dead owner's leftover
        real_rename = os.rename
        seen = {"reentered": False, "b": None}

        def rename_hook(src, dst):
            # Launcher B, at the worst possible moment: after A has decided the
            # lock is stale and before A has taken it away.
            if not seen["reentered"]:
                seen["reentered"] = True
                seen["b"] = supervisor.claim_lock(time.time())
            return real_rename(src, dst)

        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False), \
                unittest.mock.patch.object(supervisor.os, "rename", rename_hook):
            a_won = supervisor.claim_lock(time.time())
        self.assertTrue(seen["reentered"],
                        "the steal must go through an atomic rename, or two launchers can "
                        "both decide to remove the same stale lock")
        self.assertEqual(1, sum(1 for won in (a_won, seen["b"]) if won),
                         f"exactly one launcher may win: A={a_won} B={seen['b']}")
        self.assertTrue(supervisor.lock_path().exists(), "the winner must hold a lock")

    def test_a_single_launcher_always_wins_a_dead_owners_lock(self):
        # Liveness for the case that actually happens: the machine crashed, the
        # lock names a dead pid, and ONE session starts. It must get the lock,
        # every time - a watchdog that declines to start after a crash is the
        # failure this task exists to prevent.
        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False):
            for attempt in range(20):
                with self.subTest(attempt=attempt):
                    supervisor.write_lock(4242, time.time())
                    self.assertTrue(supervisor.claim_lock(time.time()))
                    self.assertIsNone(supervisor.read_lock()["pid"])

    def test_concurrent_launchers_never_both_steal_a_dead_owners_lock(self):
        """Real threads over the same path. The invariant asserted here is
        SAFETY - at most one winner - and not liveness, deliberately.

        MEASURED with four launchers released from a barrier onto one stale
        lock: sometimes zero of them win a round. The dance is real - one takes
        the stale file, another creates a lock in the gap, a third takes THAT
        one and hands it back - and the outcome is that everybody declines. That
        is the correct direction to fail: two supervisors relaunching work for
        each other is the harm this lock exists to prevent, while nobody
        starting one costs a session with no watcher, and the next session start
        steals the still-stale lock uncontended (pinned by the test above).

        Four simultaneous session starts is a test construct in any case; with
        two launchers one of them always wins. Fixing the last round of the dance
        would need a third file (a steal token with its own staleness rule), which
        is more machinery than a delayed watchdog start is worth.

        This test is NOT the one that catches a non-atomic steal - 60 races never
        landed inside the old unlink window on this host. The deterministic
        interleave above is."""
        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False):
            for attempt in range(20):
                with self.subTest(attempt=attempt):
                    # A lock whose recorded owner is dead: stealable, and every
                    # racer will agree that it is.
                    supervisor.write_lock(4242, time.time())
                    won: list[int] = []
                    barrier = threading.Barrier(4)

                    def racer():
                        barrier.wait()
                        if supervisor.claim_lock(time.time()):
                            won.append(1)

                    threads = [threading.Thread(target=racer) for _ in range(4)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(30)
                    self.assertLessEqual(len(won), 1,
                                         "more than one launcher stole the same stale lock, "
                                         "so more than one supervisor would have started")
        # No litter: the renamed-away files are removed, not accumulated.
        self.assertEqual([], list(self.tmp.glob("*.stale-*")))

    def test_taking_a_stale_lock_can_only_succeed_once(self):
        supervisor.write_lock(4242, time.time())
        stale = supervisor.read_lock()
        self.assertTrue(supervisor.take_stale_lock(stale))
        self.assertFalse(supervisor.lock_path().exists())
        # The second caller has nothing to take, which is what the loser of a
        # race sees. It must report the loss rather than carry on.
        self.assertFalse(supervisor.take_stale_lock(stale))

    def test_taking_a_lock_that_is_no_longer_the_stale_one_hands_it_back(self):
        # The content check, on its own: the file at the path was replaced
        # between the stale verdict and the steal, so the steal must fail AND
        # leave the new lock exactly where it was.
        stale = {"pid": 4242, "at": 1.0, "lane": "default"}
        supervisor.write_lock(os.getpid(), time.time())
        current = supervisor.read_lock()
        self.assertFalse(supervisor.take_stale_lock(stale))
        self.assertEqual(current, supervisor.read_lock(),
                         "a live lock that was moved must be put back untouched")
        self.assertEqual([], list(self.tmp.glob("*.stale-*")))

    def test_a_launcher_that_died_mid_start_does_not_hold_the_lock_forever(self):
        # A claim with no pid is only respected for CLAIM_GRACE. Longer than that
        # means the launcher never finished, and a lock nobody can release is
        # worse than one stolen a minute early.
        old = time.time() - supervisor.CLAIM_GRACE - 1
        supervisor.write_lock(None, old)
        kind, _, why = supervisor.owner(time.time())
        self.assertEqual(supervisor.FREE, kind)
        self.assertIn("stale claim", why)
        self.assertTrue(supervisor.claim_lock(time.time()))

    def test_a_second_session_backs_off_from_a_claim_in_progress(self):
        supervisor.claim_lock(time.time())
        with unittest.mock.patch.object(supervisor, "spawn_detached") as spawn:
            started, msg = supervisor.ensure_running()
        self.assertFalse(started)
        spawn.assert_not_called()
        self.assertIn("not started", msg)

    def test_releasing_the_lock_only_ever_drops_our_own(self):
        supervisor.write_lock(os.getpid(), time.time())
        self.assertTrue(supervisor.release_lock_if_ours())
        self.assertFalse(supervisor.lock_path().exists())
        supervisor.write_lock(os.getpid() + 1, time.time())
        self.assertFalse(supervisor.release_lock_if_ours())
        self.assertTrue(supervisor.lock_path().exists())

    def test_a_corrupt_lock_file_is_not_read_as_an_owner(self):
        supervisor.lock_path().write_text("{not json", encoding="utf-8")
        self.assertIsNone(supervisor.read_lock())
        # A file with no readable timestamp is indistinguishable from a claim
        # caught between its exclusive create and its write, so while it is FRESH
        # it is respected (that is what stops a racing launcher from stealing a
        # claim that was just won).
        self.assertEqual(supervisor.CLAIMING, supervisor.owner(time.time())[0])
        # Once it is older than the grace period nobody is mid-spawn, so it is
        # stealable rather than permanent.
        old = time.time() - supervisor.CLAIM_GRACE - 1
        os.utime(supervisor.lock_path(), (old, old))
        kind, _, why = supervisor.owner(time.time())
        self.assertEqual(supervisor.FREE, kind)
        self.assertIn("stale claim", why)
        self.assertTrue(supervisor.claim_lock(time.time()))

    def test_the_lock_is_per_lane(self):
        for suffix in ("", ".planning"):
            with self.subTest(lane=suffix or "default"):
                with unittest.mock.patch.object(state, "LANE_SUFFIX", suffix):
                    self.assertEqual(f"supervisor{suffix}.lock",
                                     supervisor.lock_path().name)


class DetachedStartTest(Sandbox):
    """It has to outlive the session that started it, and its launcher has to
    fail open. Both are proven by running them, not by reading them."""

    def test_the_daemon_survives_the_death_of_the_process_that_spawned_it(self):
        # A real three-generation test, because reasoning about process trees is
        # exactly the kind of thing that is wrong on one platform. The middle
        # process calls spawn_detached (the real one), reports the grandchild's
        # pid, and is then killed.
        sleeper = "import time; time.sleep(120)"
        script = "\n".join([
            "import sys, time",
            f"sys.path.insert(0, {str(PIPELINE_DIR)!r})",
            "import supervisor",
            f"handle = open({str(self.tmp / 'grandchild.log')!r}, 'w')",
            f"pid = supervisor.spawn_detached([sys.executable, '-c', {sleeper!r}], handle)",
            "print(pid, flush=True)",
            "time.sleep(120)",
        ])
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
        self.addCleanup(parent.stderr.close)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        line = parent.stdout.readline().strip()
        self.assertTrue(line.isdigit(), f"the spawning process printed {line!r}")
        grandchild = int(line)
        self.addCleanup(lambda: supervisor.pid_alive(grandchild) and os.kill(
            grandchild, signal.SIGTERM))
        self.assertTrue(supervisor.pid_alive(grandchild))

        parent.kill()
        parent.wait(timeout=30)
        self.assertFalse(supervisor.pid_alive(parent.pid))
        # The whole point: the watcher is still there after its spawner is gone.
        # Without that, DEAD is undetectable - the watcher dies with the session
        # whose death it is supposed to report.
        time.sleep(1.0)
        self.assertTrue(supervisor.pid_alive(grandchild),
                        "the detached daemon died with the process that spawned it")

    def test_the_daemon_survives_the_signal_that_takes_its_spawner_down(self):
        """The survival test above, made load-bearing.

        MEASURED: with the detachment removed from spawn_detached entirely (no
        creationflags on Windows, no start_new_session on POSIX), the test above
        still PASSES, because killing a process on Windows does not take its
        children with it - so it demonstrates the platform's default orphan
        behaviour rather than this code's detachment, and it cannot fail.

        What detachment actually buys is immunity to the signal that reaches a
        whole process GROUP, which is what closing the session's terminal or
        pressing Ctrl+C in it does. So this sends exactly that: the middle
        process is created in its own group, the group is signalled, and the
        grandchild must not be in it. Measured both ways - detached: survived
        3 of 3; detachment removed: died 1 of 1, deterministically, because
        group membership is inherited at creation and not a race."""
        sleeper = "import time; time.sleep(120)"
        script = "\n".join([
            "import sys, time",
            f"sys.path.insert(0, {str(PIPELINE_DIR)!r})",
            "import supervisor",
            f"handle = open({str(self.tmp / 'signalled.log')!r}, 'w')",
            f"pid = supervisor.spawn_detached([sys.executable, '-c', {sleeper!r}], handle)",
            "print(pid, flush=True)",
            "time.sleep(120)",
        ])
        # The middle process gets its OWN group, so the signal below reaches it
        # and anything it kept in that group, and never this test runner.
        flags = supervisor._CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                  text=True, creationflags=flags)
        self.addCleanup(parent.stderr.close)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        line = parent.stdout.readline().strip()
        self.assertTrue(line.isdigit(), f"the spawning process printed {line!r}")
        grandchild = int(line)
        self.addCleanup(lambda: supervisor.pid_alive(grandchild) and os.kill(
            grandchild, signal.SIGTERM))

        # The settle is load-bearing, and it cost an hour to find: a console
        # signal sent within milliseconds of the grandchild's creation is
        # delivered before its interpreter has finished starting, and it then
        # survives even WITHOUT detachment - which made this test pass under the
        # mutation it exists to catch. With the second, the mutation is caught
        # every time, in the runner and outside it.
        time.sleep(1.0)
        os.kill(parent.pid, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        try:
            parent.wait(timeout=30)
        except subprocess.TimeoutExpired:
            parent.kill()
            parent.wait(timeout=30)
        self.assertFalse(supervisor.pid_alive(parent.pid))

        time.sleep(1.5)
        self.assertTrue(supervisor.pid_alive(grandchild),
                        "the signal that killed the spawner reached the daemon too, so a "
                        "closed terminal would take the watchdog down with the session")

    def detached_kwargs(self, name):
        """spawn_detached's kwargs as they come out on a named platform. The
        platform is patched rather than read, so both branches are covered on
        either host - and so that a leaked `os.name` patch elsewhere in the suite
        cannot decide which half of this test runs (it did once: KeyError
        'creationflags' under discover)."""
        with unittest.mock.patch.object(supervisor.os, "name", name), \
                unittest.mock.patch.object(supervisor.subprocess, "Popen") as popen:
            popen.return_value = unittest.mock.Mock(pid=11)
            self.assertEqual(11, supervisor.spawn_detached(["x"], subprocess.DEVNULL))
        return popen.call_args[1]

    def test_the_spawn_is_detached_by_the_platforms_own_mechanism(self):
        windows = self.detached_kwargs("nt")
        self.assertEqual(supervisor._DETACHED_PROCESS | supervisor._CREATE_NEW_PROCESS_GROUP,
                         windows["creationflags"])
        self.assertNotIn("start_new_session", windows)

        posix = self.detached_kwargs("posix")
        self.assertIs(True, posix["start_new_session"])
        self.assertNotIn("creationflags", posix)

        for name, kwargs in (("nt", windows), ("posix", posix)):
            with self.subTest(platform=name):
                self.assertIs(False, kwargs["shell"])
                self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
                self.assertIs(True, kwargs["close_fds"])
                self.assertEqual(str(state.ROOT), kwargs["cwd"])

    def test_the_daemon_command_is_this_file_with_no_flags(self):
        argv = supervisor.daemon_argv()
        self.assertEqual(sys.executable, argv[0])
        self.assertEqual("supervisor.py", Path(argv[1]).name)
        self.assertEqual(2, len(argv), "the daemon takes its settings from pipeline.json")

    def test_the_hook_fails_open_when_the_supervisor_cannot_start(self):
        # A session that will not start because its watchdog will not start is a
        # worse failure than having no watchdog. So: exit 0, one line, and the
        # lock released rather than left claimed by a launcher that spawned
        # nothing.
        with unittest.mock.patch.object(supervisor, "spawn_detached",
                                        side_effect=OSError("no python on PATH")):
            with unittest.mock.patch.object(sys, "argv",
                                            ["supervisor.py", "--ensure-running"]):
                code = supervisor.main()
        self.assertEqual(0, code)
        self.assertFalse(supervisor.lock_path().exists(),
                         "a launcher that spawned nothing must not keep the lock")
        self.assertIn("no python on PATH", supervisor.log_path().read_text(encoding="utf-8"))

    def test_an_error_anywhere_in_the_launcher_still_exits_zero_and_says_so(self):
        # MEASURED DEFECT IN THIS TEST, fixed here: the five subtests shared one
        # state directory, and the `write_lock` subtest leaves the pid-less
        # claim its launcher made. The `daemon_argv` subtest then returned early
        # at owner() == CLAIMING and never reached daemon_argv at all - the mock
        # was never called, and the subtest passed on a completely different
        # code path. So the lock is cleared between points and the injected
        # failure now has to be REACHED. And a fail-open watchdog that says
        # nothing is indistinguishable from a working one, so each path must
        # leave its reason in the log and exactly one line in the session.
        for point in ("config", "owner", "claim_lock", "write_lock", "daemon_argv"):
            with self.subTest(raises_in=point):
                try:
                    supervisor.lock_path().unlink()
                except OSError:
                    pass
                if supervisor.log_path().exists():
                    supervisor.log_path().unlink()
                with unittest.mock.patch.object(supervisor, point,
                                                side_effect=RuntimeError("boom")) as broken, \
                        unittest.mock.patch.object(supervisor, "spawn_detached"), \
                        unittest.mock.patch("builtins.print") as printed, \
                        unittest.mock.patch.object(sys, "argv",
                                                   ["supervisor.py", "--ensure-running"]):
                    self.assertEqual(0, supervisor.main())
                self.assertTrue(broken.called, f"{point} was never reached, so this subtest "
                                               f"exited zero on some other path")
                self.assertIn("boom", supervisor.log_path().read_text(encoding="utf-8"))
                printed.assert_called_once()

    def test_the_launcher_says_nothing_on_stdout_when_it_worked(self):
        # stdout from a SessionStart hook is injected into the session's context,
        # so the happy path is silent and the log file carries the detail.
        with unittest.mock.patch.object(supervisor, "spawn_detached", return_value=os.getpid()), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", "--ensure-running"]), \
                unittest.mock.patch("builtins.print") as printed:
            self.assertEqual(0, supervisor.main())
        printed.assert_not_called()
        self.assertIn("started detached", supervisor.log_path().read_text(encoding="utf-8"))

    def test_the_normal_not_started_outcomes_are_silent_too(self):
        # A session start that finds a supervisor already running, or a project
        # that turned it off, is not an event. Printing it spends context on a
        # non-event at every single start; the log still records it.
        cases = {
            "already running": lambda: supervisor.write_lock(os.getpid(), time.time()),
            "disabled": lambda: None,
        }
        for name, prepare in cases.items():
            with self.subTest(outcome=name):
                if supervisor.log_path().exists():
                    supervisor.log_path().unlink()
                prepare()
                conf = cfg(enabled=(name != "disabled"))
                with unittest.mock.patch.object(supervisor, "config", return_value=conf), \
                        unittest.mock.patch.object(supervisor, "spawn_detached") as spawn, \
                        unittest.mock.patch("builtins.print") as printed, \
                        unittest.mock.patch.object(sys, "argv",
                                                   ["supervisor.py", "--ensure-running"]):
                    self.assertEqual(0, supervisor.main())
                spawn.assert_not_called()
                printed.assert_not_called()
                # Logged, though: silent to the session is not silent to the log.
                self.assertIn(name.split()[0],
                              supervisor.log_path().read_text(encoding="utf-8"))

    def test_a_disabled_supervisor_is_not_started_by_the_hook(self):
        with unittest.mock.patch.object(supervisor, "config", return_value=cfg(enabled=False)), \
                unittest.mock.patch.object(supervisor, "spawn_detached") as spawn:
            started, msg = supervisor.ensure_running()
        spawn.assert_not_called()
        self.assertFalse(started)
        self.assertIn("disabled", msg)
        self.assertFalse(supervisor.lock_path().exists())

    def test_the_log_file_lives_under_the_gitignored_state_directory(self):
        self.assertEqual(state.STATE_DIR, supervisor.log_path().parent)
        ignored = (state.ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".agentry/state/", ignored)


class StopTest(Sandbox):
    """Stopping is one documented command, and it releases the lock. A stop that
    leaves the lock behind would keep the next session from starting a fresh one
    until the grace period expired."""

    def test_stop_signals_the_owner_and_releases_the_lock(self):
        proc = spawn_child()
        self.addCleanup(proc.wait, 30)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        supervisor.write_lock(proc.pid, time.time())
        ok, msg = supervisor.stop()
        self.assertTrue(ok, msg)
        self.assertIn(str(proc.pid), msg)
        self.assertIn("lock released", msg)
        self.assertFalse(supervisor.lock_path().exists())
        self.assertFalse(supervisor.pid_alive(proc.pid))
        proc.wait(timeout=30)

    def test_stop_with_nothing_running_reports_it_and_clears_a_stale_lock(self):
        # The probe is faked for the same reason as in SingletonLockTest: a
        # just-reaped pid can be recycled between the two calls, and a flaky
        # test about killing processes is worse than no test.
        supervisor.write_lock(4242, time.time())
        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False), \
                unittest.mock.patch.object(supervisor.os, "kill") as kill:
            ok, msg = supervisor.stop()
        kill.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("no supervisor is running", msg)
        self.assertFalse(supervisor.lock_path().exists())

    def test_stop_never_kills_anything_but_the_recorded_owner(self):
        # The lock is the only thing that decides what gets signalled.
        with unittest.mock.patch.object(supervisor.os, "kill") as kill:
            ok, msg = supervisor.stop()
        kill.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("no lock file", msg)

    def test_stop_exits_one_when_there_was_nothing_to_stop(self):
        with unittest.mock.patch.object(sys, "argv", ["supervisor.py", "--stop"]):
            self.assertEqual(1, supervisor.main())
        self.assertTrue(any("no supervisor is running" in n for n in self.notices))


class DaemonLifecycleTest(Sandbox):
    """The daemon's own singleton behaviour and its idle exit."""

    def run_daemon(self, argv, conf=None, poll_result=None, sleeper=None):
        result = poll_result or {"events": [], "stop": ""}
        with unittest.mock.patch.object(supervisor.mode, "conveyor_runs", return_value=True), \
                unittest.mock.patch.object(supervisor, "config", return_value=conf or cfg()), \
                unittest.mock.patch.object(supervisor, "poll_once",
                                           return_value=result) as poll, \
                unittest.mock.patch.object(supervisor.time, "sleep",
                                           side_effect=sleeper or KeyboardInterrupt), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py", *argv]):
            code = supervisor.main()
        return code, poll

    def test_a_second_daemon_refuses_to_watch_the_same_lane(self):
        proc = spawn_child()
        self.addCleanup(proc.wait, 30)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        supervisor.write_lock(proc.pid, time.time())
        code, poll = self.run_daemon([])
        self.assertEqual(4, code)
        poll.assert_not_called()
        self.assertTrue(any("already holds lane" in n for n in self.notices))
        # And it left the other daemon's lock exactly as it found it.
        self.assertEqual(proc.pid, supervisor.read_lock()["pid"])

    def test_the_daemon_records_its_own_pid_and_releases_it_on_exit(self):
        recorded = []

        def sleeper(_seconds):
            recorded.append(supervisor.read_lock())
            raise KeyboardInterrupt
        code, _ = self.run_daemon([], sleeper=sleeper)
        self.assertEqual(0, code)
        self.assertEqual(os.getpid(), recorded[0]["pid"])
        self.assertFalse(supervisor.lock_path().exists(),
                         "a clean exit must release the lock, or the next session steals it")

    def test_the_daemon_adopts_the_claim_its_launcher_made_for_it(self):
        # The launcher claims the lock before the child exists, so the child sees
        # a pid-less claim. It must take that claim rather than read it as
        # another daemon and refuse.
        supervisor.claim_lock(time.time())
        seen = []

        def sleeper(_seconds):
            seen.append(supervisor.read_lock()["pid"])
            raise KeyboardInterrupt
        code, poll = self.run_daemon([], sleeper=sleeper)
        self.assertEqual(0, code)
        poll.assert_called_once()
        self.assertEqual([os.getpid()], seen)

    def test_the_daemon_refreshes_its_heartbeat_on_every_poll(self):
        # Counted rather than timed. The first version compared timestamps and
        # was flaky: three writes with a no-op sleeper land inside one clock tick
        # on Windows, whose time.time() granularity is about 16ms.
        real = supervisor.write_lock
        calls = []

        def spy(pid, at):
            calls.append(pid)
            return real(pid, at)

        def sleeper(_seconds):
            if len(calls) >= 4:
                raise KeyboardInterrupt
        with unittest.mock.patch.object(supervisor, "write_lock", spy):
            code, poll = self.run_daemon([], conf=cfg(poll_seconds=0), sleeper=sleeper)
        self.assertEqual(0, code)
        # One write to adopt the lock at startup, then one per poll. Without the
        # per-poll refresh the lock goes stale under a healthy daemon and the
        # next session steals its lane.
        self.assertEqual(poll.call_count + 1, len(calls))
        self.assertEqual({os.getpid()}, set(calls))

    def test_a_daemon_whose_lock_was_released_stops_polling(self):
        # This is what makes --stop reliable even when its signal does not land:
        # the daemon checks that it still holds the lane before every poll, so a
        # released lock stops it at the next tick rather than never.
        def sleeper(_seconds):
            supervisor.lock_path().unlink()

        code, poll = self.run_daemon([], conf=cfg(poll_seconds=0), sleeper=sleeper)
        self.assertEqual(0, code)
        self.assertEqual(1, poll.call_count, "it must not poll again after losing the lock")
        self.assertTrue(any("no longer" in n for n in self.notices))

    def test_a_daemon_that_was_replaced_gives_up_the_lane(self):
        # A long suspend stops the heartbeat without stopping the daemon, so a
        # launcher can decide the lock is stale and start a replacement. One of
        # the two has to go, and it is the old one: the newcomer holds the lock.
        newcomer = spawn_child()
        self.addCleanup(newcomer.wait, 30)
        self.addCleanup(lambda: newcomer.poll() is None and newcomer.kill())

        def sleeper(_seconds):
            supervisor.write_lock(newcomer.pid, time.time())

        code, poll = self.run_daemon([], conf=cfg(poll_seconds=0), sleeper=sleeper)
        self.assertEqual(4, code)
        self.assertEqual(1, poll.call_count)
        self.assertTrue(any("is now held by pid" in n for n in self.notices))
        # And it left the newcomer's lock alone on the way out.
        self.assertEqual(newcomer.pid, supervisor.read_lock()["pid"])

    def test_a_single_pass_never_touches_the_lock(self):
        for flags in (["--once"], ["--status"]):
            with self.subTest(flags=" ".join(flags)):
                code, poll = self.run_daemon(flags)
                self.assertEqual(0, code)
                poll.assert_called_once()
                self.assertFalse(supervisor.lock_path().exists(),
                                 "a look is not a singleton and must not claim the lock")

    def test_a_single_pass_is_allowed_while_a_daemon_holds_the_lock(self):
        proc = spawn_child()
        self.addCleanup(proc.wait, 30)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        supervisor.write_lock(proc.pid, time.time())
        code, poll = self.run_daemon(["--status"])
        self.assertEqual(0, code)
        poll.assert_called_once()

    def test_it_exits_after_the_configured_number_of_idle_polls(self):
        code, poll = self.run_daemon([], conf=cfg(idle_exit_polls=3, poll_seconds=0),
                                     sleeper=lambda _s: None)
        self.assertEqual(0, code)
        self.assertEqual(3, poll.call_count)
        self.assertFalse(supervisor.lock_path().exists())
        self.assertTrue(any("nothing in flight for 3" in n for n in self.notices))

    def test_a_single_classified_run_resets_the_idle_countdown(self):
        slept = []

        def sleeper(_seconds):
            slept.append(1)
            if len(slept) == 5:
                raise KeyboardInterrupt
        code, poll = self.run_daemon(
            [], conf=cfg(idle_exit_polls=2, poll_seconds=0), sleeper=sleeper,
            poll_result={"events": [{"task": "task-0001", "classification": "HEALTHY"}],
                         "stop": ""})
        self.assertEqual(0, code)
        self.assertEqual(5, poll.call_count,
                         "a supervisor with work to watch must not exit on the idle timer")

    def test_idle_exit_can_be_turned_off(self):
        slept = []

        def sleeper(_seconds):
            slept.append(1)
            if len(slept) == 4:
                raise KeyboardInterrupt
        code, poll = self.run_daemon([], conf=cfg(idle_exit_polls=0, poll_seconds=0),
                                     sleeper=sleeper)
        self.assertEqual(0, code)
        self.assertEqual(4, poll.call_count)


class DashTest(unittest.TestCase):
    """NFR-5: no em dash and no en dash in anything this task wrote."""

    def test_no_forbidden_dash_in_the_files_this_task_added(self):
        files = [PIPELINE_DIR / "supervisor.py",
                 Path(__file__),
                 state.ROOT / ".agentry" / "pipeline.json",
                 state.ROOT / ".claude" / "settings.json"]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for name, dash in (("em dash", EM_DASH), ("en dash", EN_DASH)):
                with self.subTest(path=path.name, dash=name):
                    self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()

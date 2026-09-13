"""Four conveyor gaps found by running the harness on itself.

Each test pins one defect that cost a round trip every turn of a real session:

1. a run parked on a human (checkpoint, or a finished task waiting for the CEO
   to merge) let stop_gate.py offer the next backlog task, which would switch
   the branch out from under a human reading the diff;
2. nothing wrote the busy marker when a subagent was dispatched, so the hook
   nagged to advance a stage whose agent was still working;
3. a BLOCKED run was excluded from the free-slot calculation, so a new task
   could start on top of its dirty working tree;
4. `done` was reached on the ABSENCE of merge evidence - "no branch found in
   any repo" was read as "nothing to merge" rather than "cannot tell";
5. the backlog reader read only `depends_on`, so `superseded_by:` and
   `blocked_on:` were decorative - the queue kept offering a task whose work had
   been folded into another one, and a dependency on such a task could never be
   satisfied because a superseded task never reaches done/;
6. a run sitting on a stage the config does not define reported success and died
   quietly: no exit_gate means "configured and passed", so advance.py wrote
   stage=NULL under an "advanced" message, and NULL is in neither the editing
   set nor awaiting_human - so the Stop hook read a free slot and offered the
   next task, which is defects 1 and 3 back through another door.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import advance
import approve
import git_state
import state
import stop_gate

# The stock build flow, as a literal: these tests must keep asserting against a
# known stage list even when .claude/pipeline.json is edited.
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


def run(task="task-0001", stage="implement", status=state.ST_IN_PROGRESS,
        awaiting_human="", **extra) -> dict:
    row = {
        "task": task, "type": "feature", "stage": stage, "stage_status": status,
        "awaiting_human": awaiting_human, "commit_approved": 0, "push_approved": 0,
        "retries": 0, "continuations": 0, "pipeline": state.BUILD,
    }
    row.update(extra)
    return row


class _FakeConn:
    def close(self):
        pass


def decide_with(runs: list[dict], backlog=("task-0002",), pipeline=None) -> str | None:
    """stop_gate.decide() over a synthetic run set. Returns the block reason, or
    None when the hook allowed the stop. No DB, no git, no task files.

    busy_marker_fresh is mocked too: it reads the real .claude/state/, so a live
    gate marker for the task id used here (written whenever the orchestrator
    dispatches a subagent for it) silenced the hook and failed these tests for
    an environmental reason. BusyMarkerTest covers that function directly."""
    queue = [{"id": t, "deps": []} for t in backlog]
    buf = io.StringIO()
    with unittest.mock.patch.object(stop_gate.mode, "conveyor_runs", return_value=True), \
            unittest.mock.patch.object(stop_gate.state, "connect", return_value=_FakeConn()), \
            unittest.mock.patch.object(stop_gate.state, "all_runs", return_value=runs), \
            unittest.mock.patch.object(stop_gate.state, "load_pipeline",
                                       return_value=pipeline or {}), \
            unittest.mock.patch.object(stop_gate.state, "set_fields"), \
            unittest.mock.patch.object(stop_gate, "read_backlog", return_value=queue), \
            unittest.mock.patch.object(stop_gate, "handoff_debt", return_value=[]), \
            unittest.mock.patch.object(stop_gate, "memory_debt", return_value=[]), \
            unittest.mock.patch.object(stop_gate, "latest_undocumented", return_value=None), \
            unittest.mock.patch.object(stop_gate, "reconcile_status_drift", return_value=([], [])), \
            unittest.mock.patch.object(stop_gate.approvals, "granted", return_value=True), \
            unittest.mock.patch.object(stop_gate, "busy_marker_fresh", return_value=False), \
            redirect_stdout(buf):
        stop_gate.decide()
    out = buf.getvalue().strip()
    return json.loads(out)["reason"] if out else None


class FreeSlotTest(unittest.TestCase):
    """Defects 1 and 3: what counts as an occupied working tree."""

    def test_empty_run_set_does_offer_the_next_task(self):
        # Control: without it the other assertions could pass vacuously.
        reason = decide_with([])
        self.assertIsNotNone(reason)
        self.assertIn("Start the next ready task task-0002", reason)

    def test_run_parked_at_a_checkpoint_offers_nothing(self):
        # Two checkpoints remain on the build flow: the commit and the push.
        for awaiting in ("commit", "push"):
            with self.subTest(awaiting_human=awaiting):
                self.assertIsNone(decide_with([run(stage="ready", awaiting_human=awaiting)]))

    def test_finished_run_waiting_for_the_ceo_merge_offers_nothing(self):
        # The reported case: task-0001 sat at 'done' waiting to be merged while
        # the hook told the orchestrator to start task-0003, then task-0002.
        self.assertIsNone(decide_with([run(stage="done", awaiting_human="merge")]))

    def test_blocked_run_offers_nothing(self):
        reason = decide_with([run(stage="implement", status=state.ST_BLOCKED)])
        self.assertIsNone(reason)

    def test_editing_run_is_still_driven_not_replaced(self):
        # Unchanged behaviour guard: an in-flight editing stage is continued.
        reason = decide_with([run(stage="implement")])
        self.assertIn("task-0001 is at stage 'implement'", reason)


class BusyMarkerTest(unittest.TestCase):
    """Defect 2: dispatching a subagent must silence the hook through a
    documented call, not hand-written marker JSON."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_state"
        self.tmp.mkdir(exist_ok=True)
        patcher = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.glob("gate-*.json"):
            f.unlink()
        self.tmp.rmdir()

    def _cli(self, *args) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", *args]), \
                redirect_stdout(buf):
            advance.main()
        return json.loads(buf.getvalue())

    def test_busy_flag_keeps_the_hook_quiet_and_idle_releases_it(self):
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

        out = self._cli("--task", "task-0007", "--busy", "implement")
        self.assertEqual("busy", out["action"])
        self.assertTrue(stop_gate.busy_marker_fresh("task-0007"))

        out = self._cli("--task", "task-0007", "--idle")
        self.assertEqual("idle", out["action"])
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

    def test_stale_marker_does_not_keep_the_hook_quiet(self):
        with unittest.mock.patch.object(advance, "GATE_TIMEOUT", 0):
            self._cli("--task", "task-0007", "--busy", "implement")
        self.assertTrue(advance.gate_marker_path("task-0007").is_file())
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))


class MergeEvidenceTest(unittest.TestCase):
    """Defect 4: nothing reaches done because evidence was absent."""

    def _fake_git(self, refs=(), merged=(), tagged=False):
        """Minimal git stand-in: which refs exist, which are in main, and
        whether main carries a '[task-NNNN]' commit."""
        def _git(repo, *args, timeout=None):
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "branch":                      # branch_for
                return 0, ""
            if args[0] == "log":                         # task_in_main
                return 0, "abc1234 [task-0043] work" if tagged else ""
            if args[0] == "rev-parse":
                return (0, args[-1]) if args[-1] in refs else (1, "")
            if args[0] == "merge-base":
                return (0, "") if args[2] in merged else (1, "")
            return 1, ""
        return _git

    def _finish(self, frontmatter, **git):
        buf = io.StringIO()
        with unittest.mock.patch.object(git_state, "repos", return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git", self._fake_git(**git)), \
                unittest.mock.patch.object(advance, "_task_frontmatter", return_value=frontmatter), \
                unittest.mock.patch.object(advance, "_merge_wait",
                                           side_effect=lambda t, r, w: {**r, "awaiting_human": w}), \
                unittest.mock.patch.object(state, "move_task", return_value=True), \
                redirect_stdout(buf):
            advance.finish_or_wait_for_merge("task-0043", run(task="task-0043", stage="done"))
        return json.loads(buf.getvalue())

    def test_no_branch_and_no_tagged_commit_parks(self):
        out = self._finish({})
        self.assertEqual("park", out["action"])
        self.assertEqual("merge", out["awaiting_human"])
        self.assertIn("both signals are missing", out["message"])
        self.assertIn("carrier branch", out["message"])
        self.assertIn("tagged commit", out["message"])
        self.assertIn("UNKNOWN, not merged", out["message"])

    def test_declared_branch_that_is_merged_reaches_done(self):
        out = self._finish({"branch": "bugfix/task-0001"},
                           refs=("origin/bugfix/task-0001",),
                           merged=("origin/bugfix/task-0001",))
        self.assertEqual("done", out["action"])
        self.assertIn("Merged into main", out["message"])

    def test_declared_branch_that_is_unmerged_parks(self):
        out = self._finish({"branch": "bugfix/task-0001"},
                           refs=("origin/bugfix/task-0001",))
        self.assertEqual("park", out["action"])
        self.assertEqual("merge", out["awaiting_human"])
        self.assertIn("does not carry this task yet", out["message"])


class BacklogFilterTest(unittest.TestCase):
    """Defect 5: which frontmatter fields take a task out of the ready set.

    Runs against a temporary tasks tree, never the live one - a test in this
    suite already read live state once and failed for an environmental reason."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_tasks"
        for name in ("backlog", "active", "done"):
            (self.tmp / name).mkdir(parents=True, exist_ok=True)
        for attr, name in (("BACKLOG_DIR", "backlog"), ("ACTIVE_DIR", "active"),
                           ("DONE_DIR", "done")):
            p = unittest.mock.patch.object(stop_gate, attr, self.tmp / name)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for d in ("backlog", "active", "done"):
            for f in (self.tmp / d).glob("*.md"):
                f.unlink()
            (self.tmp / d).rmdir()
        self.tmp.rmdir()

    def _task(self, task, where="backlog", deps=(), blocked_on="", superseded_by=""):
        (self.tmp / where / f"{task}.md").write_text(
            "---\n"
            f"id: {task.split('-')[1]}\n"
            f"depends_on: [{', '.join(deps)}]\n"
            f"blocked_on:{(' ' + blocked_on) if blocked_on else ''}\n"
            f"superseded_by:{(' ' + superseded_by) if superseded_by else ''}\n"
            "---\n\n"
            "## Body\n\n"
            "Prose that quotes `blocked_on:` and `superseded_by:` as field names.\n",
            encoding="utf-8")

    def _ready(self):
        return [t["id"] for t in stop_gate.read_backlog()
                if all(stop_gate.dep_satisfied(d, {}) for d in t["deps"])]

    def test_blank_fields_still_offer_the_task(self):
        # Control: the template ships both fields blank on every task.
        self._task("task-0100")
        self.assertEqual(["task-0100"], self._ready())

    def test_superseded_task_is_not_offered(self):
        self._task("task-0100", superseded_by="task-0200")
        self.assertEqual([], self._ready())

    def test_non_empty_blocked_on_is_not_offered(self):
        self._task("task-0100", blocked_on="CEO ruling on the pricing model")
        self.assertEqual([], self._ready())

    def test_dependency_on_a_superseded_task_resolves_through_its_successor(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200", where="done")
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_dependency_on_a_superseded_task_waits_for_the_unfinished_successor(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200")  # successor still queued, not done
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0200"], self._ready())

    def test_dangling_supersede_pointer_does_not_hang_the_dependent_task(self):
        self._task("task-0100", superseded_by="task-0900")  # no such file anywhere
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_supersede_cycle_does_not_hang_the_dependent_task(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200", superseded_by="task-0100")
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_ordinary_unfinished_dependency_still_blocks(self):
        self._task("task-0100")
        self._task("task-0101", deps=("task-0102",))  # never created
        self.assertEqual(["task-0100"], self._ready())


class StrandedStageTest(unittest.TestCase):
    """Defect 6: a stage absent from the config counted as a passed gate.

    Runs against a throwaway run.db and a literal stage list - never the live
    state - so the assertions cannot be moved by a pipeline.json edit."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_run"
        self.tmp.mkdir(exist_ok=True)
        for attr, value in (("STATE_DIR", self.tmp), ("DB_PATH", self.tmp / "run.db")):
            p = unittest.mock.patch.object(state, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.iterdir():
            f.unlink()
        self.tmp.rmdir()

    def _seed(self, task, stage):
        conn = state.connect()
        state.create_run(conn, task, "feature", stage)
        conn.close()

    def _row(self, task) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def _cli(self, module, *args) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["cli.py", *args]), \
                redirect_stdout(buf):
            module.main()
        return json.loads(buf.getvalue())

    def test_unknown_stage_blocks_instead_of_advancing(self):
        self._seed("task-0500", "ghost-stage")
        out = self._cli(advance, "--task", "task-0500")

        self.assertEqual("blocked", out["action"])
        self.assertIn("'ghost-stage' is not part of the build flow", out["message"])
        self.assertIn("implement, test, review, ready, done", out["message"])
        self.assertIn("--reject", out["message"])

    def test_unknown_stage_never_writes_a_null_stage(self):
        self._seed("task-0500", "ghost-stage")
        for _ in range(2):  # the second call must not drift further either
            self._cli(advance, "--task", "task-0500")
            row = self._row("task-0500")
            self.assertEqual("ghost-stage", row["stage"])
            self.assertEqual(state.ST_BLOCKED, row["stage_status"])

    def test_stop_hook_offers_nothing_while_a_run_is_stranded(self):
        for status in (state.ST_IN_PROGRESS, state.ST_BLOCKED):
            with self.subTest(stage_status=status):
                self.assertIsNone(decide_with([run(stage="ghost-stage", status=status)],
                                              pipeline=BUILD_PIPELINE))

    def test_an_unreadable_pipeline_disables_the_check_rather_than_stranding(self):
        # Fail-open control: with no readable stage list the hook behaves as
        # before, so a broken config cannot freeze the queue.
        reason = decide_with([run(stage="ghost-stage")], pipeline={})
        self.assertIn("Start the next ready task task-0002", reason)

    def test_a_configured_stage_still_advances(self):
        self._seed("task-0501", "implement")
        out = self._cli(advance, "--task", "task-0501")

        self.assertEqual("advanced", out["action"])
        self.assertEqual("test", self._row("task-0501")["stage"])

    def test_the_terminal_stage_still_routes_to_the_merge_check(self):
        self._seed("task-0502", "done")
        with unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                        return_value=0) as finish, \
                unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", "task-0502"]), \
                redirect_stdout(io.StringIO()):
            advance.main()

        self.assertEqual(1, finish.call_count)
        self.assertEqual("done", self._row("task-0502")["stage"])

    def test_reject_recovers_a_stranded_run(self):
        self._seed("task-0500", "ghost-stage")
        self._cli(advance, "--task", "task-0500")

        out = self._cli(approve, "--task", "task-0500", "--reject")

        self.assertTrue(out["ok"])
        row = self._row("task-0500")
        self.assertEqual("implement", row["stage"])
        self.assertEqual(state.ST_IN_PROGRESS, row["stage_status"])


if __name__ == "__main__":
    unittest.main()

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
   any repo" was read as "nothing to merge" rather than "cannot tell".
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
import git_state
import state
import stop_gate


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


def decide_with(runs: list[dict], backlog=("task-0002",)) -> str | None:
    """stop_gate.decide() over a synthetic run set. Returns the block reason, or
    None when the hook allowed the stop. No DB, no git, no task files."""
    queue = [{"id": t, "deps": []} for t in backlog]
    buf = io.StringIO()
    with unittest.mock.patch.object(stop_gate.mode, "conveyor_runs", return_value=True), \
            unittest.mock.patch.object(stop_gate.state, "connect", return_value=_FakeConn()), \
            unittest.mock.patch.object(stop_gate.state, "all_runs", return_value=runs), \
            unittest.mock.patch.object(stop_gate.state, "load_pipeline", return_value={}), \
            unittest.mock.patch.object(stop_gate.state, "set_fields"), \
            unittest.mock.patch.object(stop_gate, "read_backlog", return_value=queue), \
            unittest.mock.patch.object(stop_gate, "handoff_debt", return_value=[]), \
            unittest.mock.patch.object(stop_gate, "memory_debt", return_value=[]), \
            unittest.mock.patch.object(stop_gate, "latest_undocumented", return_value=None), \
            unittest.mock.patch.object(stop_gate, "reconcile_status_drift", return_value=([], [])), \
            unittest.mock.patch.object(stop_gate.approvals, "granted", return_value=True), \
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
        for awaiting in ("commit", "push", "diff-review"):
            with self.subTest(awaiting_human=awaiting):
                stage = "diff-review" if awaiting == "diff-review" else "ready"
                self.assertIsNone(decide_with([run(stage=stage, awaiting_human=awaiting)]))

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


if __name__ == "__main__":
    unittest.main()

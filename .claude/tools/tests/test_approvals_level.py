"""The approvals level alone can clear a checkpoint (task-0083).

advance.py read `if pending in auto and approvals.granted(pending, sorted(auto))`,
where `auto` is the stage's `auto_approve` array from pipeline.json - which ships
empty. The leading conjunct made the stage list MANDATORY, so the level dial
could never approve anything on its own: `manual`, `assisted` and `auto` behaved
identically at `ready`, and a CEO who set `auto` for a night run was asked for
the commit anyway.

These tests pin both halves: the level alone clears the commit, and nothing -
level, stage list, or both together - clears the push.
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
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approvals
import pretool_gate
import state
import tmproot

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The shipped shape, as a literal: `ready` carries both checkpoints and NO
# auto_approve key, which is the configuration the defect was invisible under.
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


class LevelDrivesTheCheckpointTest(unittest.TestCase):

    def setUp(self):
        self.pipeline = BUILD_PIPELINE
        self.tmp = tmproot.sandbox(self, "approvals_level")
        for attr, value in (("STATE_DIR", self.tmp), ("DB_PATH", self.tmp / "run.db")):
            self._patch(state, attr, value)
        self._patch(state, "load_pipeline", side_effect=lambda: self.pipeline)
        # pr mode, independent of the real pipeline.json: solo drops the push
        # checkpoint, and these tests are about what clears it, not about it.
        self._patch(pretool_gate, "workflow_mode", return_value=pretool_gate.WORKFLOW_PR)

    def _patch(self, target, attr, value=None, **kwargs):
        if kwargs:
            p = unittest.mock.patch.object(target, attr, **kwargs)
        else:
            p = unittest.mock.patch.object(target, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def _level(self, level):
        self._patch(approvals, "read", return_value=level)

    def _seed(self, task, stage="ready"):
        conn = state.connect()
        state.create_run(conn, task, "feature", stage)
        conn.close()

    def _row(self, task) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def _advance(self, task) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", task]), \
                redirect_stdout(buf):
            advance.main()
        return json.loads(buf.getvalue())

    def _with_auto_approve(self, *checkpoints):
        stages = [dict(s) for s in BUILD_PIPELINE["pipelines"]["build"]["stages"]]
        for stage in stages:
            if stage["name"] == "ready":
                stage["auto_approve"] = list(checkpoints)
        self.pipeline = {"retry_budget": 3,
                         "pipelines": {"build": {"stages": stages}}}

    # --- the defect itself -------------------------------------------------

    def test_auto_level_clears_the_commit_with_an_empty_auto_approve(self):
        """RED before the fix: `pending in auto` was False, so nothing was
        approved and the run parked on the CEO at level `auto`."""
        self._level(approvals.AUTO)
        self._seed("task-0700")

        out = self._advance("task-0700")

        self.assertEqual(1, self._row("task-0700")["commit_approved"])
        self.assertIn("auto-approved", out["message"])
        self.assertNotIn("wait for", out["message"].lower())

    def test_the_three_levels_differ_at_ready(self):
        expected = {approvals.MANUAL: 0, approvals.ASSISTED: 1, approvals.AUTO: 1}
        for i, (level, commit_approved) in enumerate(expected.items()):
            with self.subTest(level=level):
                with unittest.mock.patch.object(approvals, "read", return_value=level):
                    task = f"task-072{i}"
                    self._seed(task)
                    self._advance(task)
                    row = self._row(task)
                    self.assertEqual(commit_approved, row["commit_approved"])
                    self.assertEqual(0, row["push_approved"])

    def test_manual_still_parks_and_says_so(self):
        self._level(approvals.MANUAL)
        self._seed("task-0701")

        out = self._advance("task-0701")

        self.assertEqual("park", out["action"])
        self.assertEqual("commit", out["awaiting_human"])
        self.assertIn("CEO commit approval", out["message"])

    def test_a_stage_list_alone_still_clears_the_commit_at_manual(self):
        """The layering the conjunction was hiding behind: with the level at its
        strictest, the stage key must still work on its own."""
        self._level(approvals.MANUAL)
        self._with_auto_approve("commit")
        self._seed("task-0702")

        self._advance("task-0702")

        self.assertEqual(1, self._row("task-0702")["commit_approved"])

    # --- NEVER_GRANTED still wins over both inputs -------------------------

    def test_push_is_refused_at_auto_with_push_in_auto_approve(self):
        self._level(approvals.AUTO)
        self._with_auto_approve("commit", "push")
        self._seed("task-0703")

        self._advance("task-0703")                      # clears the commit
        out = self._advance("task-0703")                # parks on the push

        self.assertEqual("park", out["action"])
        self.assertEqual("push", out["awaiting_human"])
        self.assertEqual(0, self._row("task-0703")["push_approved"])
        self.assertIn("CEO push approval", out["message"])

    def test_push_is_refused_at_every_level(self):
        for i, level in enumerate(approvals.LEVELS):
            with self.subTest(level=level):
                with unittest.mock.patch.object(approvals, "read", return_value=level):
                    task = f"task-071{i}"
                    self._seed(task)
                    self._advance(task)
                    self._advance(task)
                    self.assertEqual(0, self._row(task)["push_approved"])


class ShippedConfigTest(unittest.TestCase):
    """Read the REAL .agentry/pipeline.json, not a fixture: `forbid_dev_null`
    shipped switched off for six days behind three documents claiming otherwise.
    The level is what drives the commit here, so the stage key stays empty."""

    def test_ready_ships_without_an_auto_approve_list(self):
        config = json.loads((PROJECT_ROOT / ".agentry" / "pipeline.json")
                            .read_text(encoding="utf-8"))
        stages = config["pipelines"]["build"]["stages"]
        ready = next(s for s in stages if s["name"] == "ready")
        self.assertEqual(["commit", "push"], ready["checkpoints"])   # control
        self.assertFalse(ready.get("auto_approve"))


if __name__ == "__main__":
    unittest.main()

"""task-0153: which runs wait for the User, across every lane (waiting.py).

One definition shared by the Stop hook, the status line and the per-prompt reminder.
The parity test pins it to stop_gate.owed_to_ceo, the rule it was extracted from."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import itertools
import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "dashboard"))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
import state
import stop_gate
import stores
import tmproot
import waiting


def run(task="task-0900", stage="ready", status="in_progress", awaiting="", updated="2026-10-22T10:00:00Z",
        **flags):
    row = {"task": task, "stage": stage, "stage_status": status, "awaiting_human": awaiting,
           "updated": updated, "commit_approved": 0, "push_approved": 0, "plan_approved": 0}
    row.update(flags)
    return row


class PredicateTest(unittest.TestCase):

    def test_a_parked_checkpoint_is_owed_under_its_own_name(self):
        for awaiting in ("commit", "push", "plan"):
            with self.subTest(awaiting=awaiting):
                self.assertEqual(awaiting, waiting.needs_you(run(awaiting=awaiting), solo=True))

    def test_a_paused_run_owes_nothing_even_with_a_park_reason(self):
        self.assertEqual("", waiting.needs_you(run(status="paused", awaiting="plan"), solo=False))

    def test_a_recorded_approval_is_no_longer_owed(self):
        self.assertEqual("", waiting.needs_you(run(awaiting="commit", commit_approved=1), solo=False))
        self.assertEqual("", waiting.needs_you(run(awaiting="plan", plan_approved=1), solo=False))

    def test_a_blocked_run_owes_an_unblock(self):
        self.assertEqual("unblock", waiting.needs_you(run(status="blocked", awaiting="blocked"), solo=True))
        self.assertEqual("unblock", waiting.needs_you(run(status="blocked"), solo=True))

    def test_an_abandoned_run_owes_nothing(self):
        self.assertEqual("", waiting.needs_you(run(stage="done", status="abandoned"), solo=False))

    def test_the_merge_wait_is_owed_only_in_pr_mode_and_only_at_done(self):
        merge = run(stage="done", awaiting="merge")
        self.assertEqual("merge", waiting.needs_you(merge, solo=False))
        self.assertEqual("", waiting.needs_you(merge, solo=True))

    def test_a_done_run_with_another_reason_owes_nothing(self):
        self.assertEqual("", waiting.needs_you(run(stage="done", awaiting="commit"), solo=False))

    def test_an_unknown_reason_is_reported_as_stored(self):
        self.assertEqual("something-new", waiting.needs_you(run(awaiting="something-new"), solo=False))

    def test_the_blocked_marker_alone_is_not_a_checkpoint(self):
        self.assertEqual("", waiting.owed_reason(run(awaiting="blocked"), solo=False))


def frozen_owed(r: dict, solo: bool) -> bool:
    """The predicate stop_gate.owed_to_ceo had before task-0153 moved it to waiting.py,
    copied verbatim in meaning. It is the oracle: both the shared module and the Stop
    hook must still answer exactly as this does."""
    aw = r["awaiting_human"]
    if r["stage_status"] == "paused":
        return False
    flag = {"commit": "commit_approved", "push": "push_approved", "plan": "plan_approved"}.get(aw, "")
    if not aw or aw == "blocked" or r.get(flag):
        return False
    if r["stage"] == "done" and aw != "merge":
        return False
    if aw == "merge" and solo:
        return False
    return True


class ParityWithTheOldPredicateTest(unittest.TestCase):
    """A frozen copy of the old predicate is the oracle, so that a later edit to waiting.py
    cannot move the Stop hook and the status line together without a test noticing."""

    def test_owed_reason_and_owed_to_ceo_both_match_the_frozen_predicate_on_the_full_grid(self):
        grid = itertools.product(("implement", "ready", "done"), ("in_progress", "paused", "blocked"),
                                 ("", "commit", "push", "plan", "merge", "blocked", "weird"),
                                 (0, 1), (0, 1), (0, 1), (True, False))
        for stage, status, awaiting, commit, push, plan, solo in grid:
            row = run(stage=stage, status=status, awaiting=awaiting, commit_approved=commit,
                      push_approved=push, plan_approved=plan)
            expected = frozen_owed(row, solo)
            with self.subTest(stage=stage, status=status, awaiting=awaiting, approved=(commit, push, plan),
                              solo=solo):
                self.assertEqual(expected, bool(waiting.owed_reason(row, solo)))
                with unittest.mock.patch.object(stop_gate.pretool_gate, "workflow_mode",
                                                lambda s=solo: "solo" if s else "pr"):
                    self.assertEqual(expected, bool(stop_gate.owed_to_ceo([row])))


class MirroredConstantsTest(unittest.TestCase):
    """waiting.py imports none of state, stop_gate, pretool_gate and stores (their import cost is the
    reason it exists), so it mirrors a few of their constants. A drift would be silent."""

    def test_the_mirrored_constants_equal_their_originals(self):
        self.assertEqual(state.ST_PAUSED, waiting.STATUS_PAUSED)
        self.assertEqual(state.ST_BLOCKED, waiting.STATUS_BLOCKED)
        self.assertEqual(state.ST_ABANDONED, waiting.STATUS_ABANDONED)
        self.assertEqual(state.DEFAULT_LANE, waiting.DEFAULT_LANE)
        self.assertEqual(stores.DEFAULT_LANE, waiting.DEFAULT_LANE)
        self.assertEqual(stop_gate.AWAITING_BLOCKED, waiting.AWAITING_BLOCKED)
        self.assertEqual(pretool_gate.WORKFLOW_SOLO, waiting.WORKFLOW_SOLO)
        self.assertEqual(stores.LANE_DB_RE.pattern, waiting.LANE_DB_RE.pattern)

    def test_the_default_paths_are_the_ones_state_uses(self):
        self.assertEqual(state.STATE_DIR, waiting.paths.PROJECT_ROOT / ".agentry" / "state")
        self.assertEqual(state.PIPELINE_PATH, waiting.paths.PROJECT_ROOT / ".agentry" / "pipeline.json")

    def test_every_approval_column_is_a_runs_column(self):
        conn = sqlite3.connect(":memory:")
        state.init_db(conn)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        conn.close()
        self.assertLessEqual(set(waiting.APPROVED_FLAG.values()), columns)


class StoresTest(unittest.TestCase):

    def setUp(self):
        self.dir = tmproot.sandbox(self, "waiting_")

    def add(self, name, *runs):
        conn = sqlite3.connect(self.dir / name)
        try:
            state.init_db(conn)
            for r in runs:
                conn.execute(
                    "INSERT INTO runs (task, type, stage, stage_status, awaiting_human, pipeline, updated, "
                    "commit_approved, push_approved, plan_approved) "
                    "VALUES (?, 'feature', ?, ?, ?, 'build', ?, ?, ?, ?)",
                    (r["task"], r["stage"], r["stage_status"], r["awaiting_human"], r["updated"],
                     r["commit_approved"], r["push_approved"], r["plan_approved"]))
            conn.commit()
        finally:
            conn.close()

    def test_every_lane_is_read_and_the_oldest_wait_comes_first(self):
        self.add("run.db", run("task-0200", awaiting="commit", updated="2026-10-22T12:00:00Z"))
        self.add("run.docs.db", run("task-0100", awaiting="plan", updated="2026-10-22T09:00:00Z"),
                 run("task-0300", stage="implement"))
        found = waiting.waiting_runs(self.dir, solo=True)
        self.assertEqual([("task-0100", "docs", "plan"), ("task-0200", "default", "commit")],
                         [(r["task"], r["lane"], r["kind"]) for r in found])

    def test_a_lane_named_default_does_not_replace_the_default_store(self):
        self.add("run.db", run("task-0100", awaiting="plan"))
        self.add("run.default.db", run("task-0200", awaiting="commit"))
        found = waiting.waiting_runs(self.dir, solo=True)
        self.assertEqual({"task-0100", "task-0200"}, {r["task"] for r in found})

    def test_control_characters_in_a_stored_value_are_replaced_once(self):
        self.add("run.db", run("task-0100\x1b[31m", awaiting="pl\nan", updated="2026\x07-10"))
        [found] = waiting.waiting_runs(self.dir, solo=True)
        self.assertEqual(("task-0100 [31m", "pl an", "2026 -10"),
                         (found["task"], found["kind"], found["since"]))

    def test_a_backup_file_and_a_foreign_file_are_not_lanes(self):
        self.add("run.db")
        self.add("run.db.bak-0124", run("task-0100", awaiting="plan"))
        self.add("runbook.db", run("task-0101", awaiting="plan"))
        self.assertEqual([], waiting.waiting_runs(self.dir, solo=True))

    def test_a_corrupt_store_and_a_missing_table_are_skipped_not_raised(self):
        (self.dir / "run.db").write_bytes(b"this is not a database" * 50)
        sqlite3.connect(self.dir / "run.other.db").close()
        self.add("run.good.db", run("task-0100", awaiting="plan"))
        self.assertEqual(["task-0100"], [r["task"] for r in waiting.waiting_runs(self.dir, solo=True)])

    def test_a_missing_state_dir_is_no_runs(self):
        self.assertEqual([], waiting.waiting_runs(self.dir / "absent", solo=True))

    def test_reading_never_writes_and_never_creates_a_store(self):
        self.add("run.db", run(awaiting="commit"))
        before = (self.dir / "run.db").read_bytes()
        names = sorted(p.name for p in self.dir.iterdir())
        waiting.waiting_runs(self.dir, solo=True)
        self.assertEqual(before, (self.dir / "run.db").read_bytes())
        self.assertEqual(names, sorted(p.name for p in self.dir.iterdir()))

    def test_every_store_is_opened_with_mode_ro(self):
        self.add("run.db", run(awaiting="commit"))
        real, seen = sqlite3.connect, []

        def spy(target, *args, **kwargs):
            seen.append(target)
            return real(target, *args, **kwargs)

        with unittest.mock.patch.object(sqlite3, "connect", spy):
            waiting.waiting_runs(self.dir, solo=True)
        self.assertTrue(seen and all(str(t).endswith("?mode=ro") for t in seen), seen)


class WorkflowModeTest(unittest.TestCase):

    def setUp(self):
        self.dir = tmproot.sandbox(self, "waiting_cfg_")

    def test_solo_only_when_configured_so(self):
        cfg = self.dir / "pipeline.json"
        cases = (('{"workflow": {"mode": "solo"}}', True), ('{"workflow": {"mode": "pr"}}', False),
                 ('{"workflow": {"mode": "Solo "}}', True), ('{"workflow": 3}', False),
                 ("not json", False), ("[]", False))
        for text, expected in cases:
            with self.subTest(text=text):
                cfg.write_text(text, encoding="utf-8")
                self.assertEqual(expected, waiting.is_solo(cfg))
        self.assertFalse(waiting.is_solo(self.dir / "missing.json"))


if __name__ == "__main__":
    unittest.main()

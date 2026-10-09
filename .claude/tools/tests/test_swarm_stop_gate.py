"""task-0117: the Stop hook honours the swarm dial, and an umbrella task takes its
subtasks to done/ with it."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import state
import stop_gate
import tmproot
from test_conveyor_gaps import decide_output_with, run

EPIC = "epic-0007"


def task(task_id, epic="", umbrella=False):
    return {"id": task_id, "deps": [], "epic": epic, "umbrella": umbrella}


def decide(runs=(), queue=(), swarm=True, held=(), level=None, **kw):
    with unittest.mock.patch.object(stop_gate, "swarm_on", return_value=swarm), \
            unittest.mock.patch.object(stop_gate, "umbrella_epics", return_value=set(held)):
        return decide_output_with(list(runs), queue=list(queue), level=level, **kw)


PAIR = [task("task-0028", EPIC), task("task-0029", EPIC)]
SWARM_WORDS = ("skills/swarm/SKILL.md", "umbrella: true")
HANDOFF = [{"task": "task-0067", "reason": "no handoff doc"}]
LATEST = {"task": "task-0066", "sha": "a" * 40, "reason": "no handoff doc"}


class SwarmQueueTest(unittest.TestCase):

    def test_swarm_on_epic_task_demands_the_swarm_not_a_single_start(self):
        reason = decide(queue=PAIR)["reason"]
        self.assertNotIn("Start the next ready task", reason)
        self.assertIn(EPIC, reason)
        for word in SWARM_WORDS:
            self.assertIn(word, reason)

    def test_swarm_demand_carries_a_human_system_message(self):
        out = decide(queue=PAIR)
        human = out["systemMessage"]
        self.assertIn(EPIC, human)
        for word in ("advance.py", "approve.py", "AskUserQuestion", "Do not ask"):
            self.assertNotIn(word, human)
        self.assertNotEqual(out["reason"], human)

    def test_swarm_demand_only_at_the_auto_level(self):
        self.assertIsNone(decide(queue=PAIR, level="assisted"))
        self.assertIn("skills/swarm/SKILL.md", decide(queue=PAIR, level="auto")["reason"])

    def test_swarm_on_task_without_epic_is_started_as_a_single_task(self):
        out = decide(queue=[task("task-0030")])
        self.assertIn("Start the next ready task task-0030", out["reason"])

    def test_swarm_on_lone_epic_subtask_is_started_as_a_single_task(self):
        out = decide(queue=[task("task-0028", EPIC)])
        self.assertIn("Start the next ready task task-0028", out["reason"])

    def test_swarm_on_queued_umbrella_is_started_instead_of_its_subtask(self):
        out = decide(queue=[task("task-0028", EPIC), task("task-0100", EPIC, umbrella=True)])
        self.assertIn("Start the next ready task task-0100", out["reason"])

    def test_swarm_on_queued_umbrella_waiting_on_deps_blocks_its_subtasks(self):
        blocked_umbrella = {**task("task-0100", EPIC, umbrella=True), "deps": ["task-9999"]}
        self.assertIsNone(decide(queue=[*PAIR, blocked_umbrella]))

    def test_swarm_on_registered_umbrella_hides_its_epic_subtasks(self):
        # Umbrella run finished its editing stages: nothing holds the tree, the
        # file move has not happened yet, and the subtasks must not be offered.
        out = decide(runs=[run(task="task-0100", stage="done")], queue=PAIR, held=[EPIC])
        self.assertIsNone(out)

    def test_swarm_on_registered_umbrella_still_offers_other_epics(self):
        out = decide(runs=[run(task="task-0100", stage="done")],
                     queue=[*PAIR, task("task-0040", "epic-0008"), task("task-0041", "epic-0008")],
                     held=[EPIC])
        self.assertIn("epic-0008", out["reason"])

    def test_swarm_on_umbrella_run_in_flight_is_driven_as_today(self):
        out = decide(runs=[run(task="task-0100", stage="implement")], queue=PAIR, held=[EPIC])
        self.assertIn("task-0100", out["reason"])
        self.assertNotIn("task-0028", out["reason"])

    def test_swarm_off_epic_task_is_started_as_a_single_task(self):
        out = decide(queue=PAIR, swarm=False)
        self.assertIn("Start the next ready task task-0028", out["reason"])
        self.assertNotIn("swarm", out["reason"].lower())


class SwarmDebtTest(unittest.TestCase):
    """The debt demands end in a start command; for a swarm epic that must be the
    swarm start, never `advance.py --task <subtask>`."""


    def assert_swarm_tail(self, out):
        reason = out["reason"]
        for word in SWARM_WORDS:
            self.assertIn(word, reason)
        self.assertNotIn("--task task-0028", reason)
        self.assertTrue(reason.endswith("Do not ask the user."), reason)

    def test_handoff_debt_ends_in_the_swarm_start(self):
        out = decide(queue=PAIR, debt=HANDOFF)
        self.assertIn("no valid handoff doc", out["reason"])
        self.assert_swarm_tail(out)

    def test_memory_debt_ends_in_the_swarm_start(self):
        out = decide(queue=PAIR, mem_debt=[{"task": "task-0067"}])
        self.assertIn("distilled", out["reason"])
        self.assert_swarm_tail(out)

    def test_main_handoff_debt_ends_in_the_swarm_start(self):
        waiting = {**task("task-0029", EPIC), "deps": ["task-9999"]}
        out = decide(queue=[task("task-0028", EPIC), waiting], latest=LATEST)
        self.assertIn("the only ready task", out["reason"])
        self.assert_swarm_tail(out)

    def test_debts_for_a_plain_task_still_end_in_advance(self):
        for kw in ({"debt": HANDOFF}, {"mem_debt": [{"task": "task-0067"}]}):
            with self.subTest(**kw):
                out = decide(queue=[task("task-0030")], **kw)
                self.assertTrue(out["reason"].endswith(
                    "run: python .claude/tools/pipeline/advance.py --task task-0030. "
                    "Do not ask the user."), out["reason"])

    def test_swarm_off_debts_for_epic_subtasks_still_end_in_advance(self):
        out = decide(queue=PAIR, debt=HANDOFF, swarm=False)
        self.assertIn("advance.py --task task-0028. Do not ask the user.", out["reason"])


class SwarmFailOpenTest(unittest.TestCase):

    def test_unreadable_dial_means_off(self):
        with unittest.mock.patch.object(stop_gate.swarm, "read", side_effect=RuntimeError):
            self.assertFalse(stop_gate.swarm_on())

    def test_dial_on_is_read(self):
        with unittest.mock.patch.object(stop_gate.swarm, "read", return_value="on"):
            self.assertTrue(stop_gate.swarm_on())


class FrontmatterReadTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "swarm_stop_")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            self.dirs[name] = self.tmp / name
            self.dirs[name].mkdir()

    def write(self, folder, name, head, body=""):
        (self.dirs[folder] / f"{name}.md").write_text(
            f"---\n{head}\n---\n{body}", encoding="utf-8")

    def umbrella_epics(self, runs):
        with unittest.mock.patch.object(stop_gate, "ACTIVE_DIR", self.dirs["active"]), \
                unittest.mock.patch.object(stop_gate, "DONE_DIR", self.dirs["done"]), \
                unittest.mock.patch.object(stop_gate, "BACKLOG_DIR", self.dirs["backlog"]):
            return stop_gate.umbrella_epics(runs)

    def test_read_backlog_reports_epic_and_umbrella(self):
        self.write("backlog", "task-0001", f"id: 1\nepic: {EPIC}\numbrella: true")
        self.write("backlog", "task-0002", f"id: 2\nepic: {EPIC}")
        self.write("backlog", "task-0003", "id: 3\nepic:\n# umbrella: true",
                   "umbrella: true\nepic: epic-0099\n")
        with unittest.mock.patch.object(stop_gate, "BACKLOG_DIR", self.dirs["backlog"]):
            queue = {t["id"]: t for t in stop_gate.read_backlog()}
        self.assertEqual((EPIC, True), (queue["task-0001"]["epic"], queue["task-0001"]["umbrella"]))
        self.assertEqual((EPIC, False), (queue["task-0002"]["epic"], queue["task-0002"]["umbrella"]))
        self.assertEqual(("", False), (queue["task-0003"]["epic"], queue["task-0003"]["umbrella"]))

    def test_umbrella_epics_reads_the_run_task_files(self):
        self.write("active", "task-0100", f"id: 100\nepic: {EPIC}\numbrella: true")
        self.write("active", "task-0101", "id: 101\nepic: epic-0008")
        held = self.umbrella_epics([run(task="task-0100"), run(task="task-0101"),
                                    run(task="task-0999")])
        self.assertEqual({EPIC}, held)

    def test_umbrella_in_done_no_longer_holds_its_epic(self):
        self.write("done", "task-0100", f"id: 100\nepic: {EPIC}\numbrella: true")
        self.assertEqual(set(), self.umbrella_epics([run(task="task-0100")]))


class UmbrellaDoneMovesSubtasksTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "swarm_done_")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            self.dirs[name] = self.tmp / name
            self.dirs[name].mkdir()
        patcher = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, folder, name, head):
        (self.dirs[folder] / f"{name}.md").write_text(
            f"---\n{head}\n---\nbody\n", encoding="utf-8")

    def umbrella(self, subtasks="[task-0028, task-0029]", flag="true"):
        self.write("done", "task-0100",
                   f"id: 100\nepic: {EPIC}\numbrella: {flag}\nsubtasks: {subtasks}\ncompleted:")

    def where(self, name):
        return state.task_dir(name)

    def test_listed_subtasks_of_the_epic_move_to_done(self):
        self.umbrella()
        self.write("backlog", "task-0028", f"id: 28\nepic: {EPIC}\ncompleted:")
        self.write("active", "task-0029", f"id: 29\nepic: {EPIC}\ncompleted:")
        self.assertEqual(["task-0028", "task-0029"], state.move_swarm_subtasks("task-0100"))
        self.assertEqual("done", self.where("task-0028"))
        self.assertEqual("done", self.where("task-0029"))

    def test_unlisted_subtask_and_other_epic_subtask_stay(self):
        self.umbrella(subtasks="[task-0028, task-0031]")
        self.write("backlog", "task-0028", f"id: 28\nepic: {EPIC}")
        self.write("backlog", "task-0030", f"id: 30\nepic: {EPIC}")
        self.write("backlog", "task-0031", "id: 31\nepic: epic-0008")
        self.assertEqual(["task-0028"], state.move_swarm_subtasks("task-0100"))
        self.assertEqual("backlog", self.where("task-0030"))
        self.assertEqual("backlog", self.where("task-0031"))

    def test_ordinary_task_moves_nothing(self):
        self.umbrella(flag="false")
        self.write("backlog", "task-0028", f"id: 28\nepic: {EPIC}")
        self.assertEqual([], state.move_swarm_subtasks("task-0100"))
        self.assertEqual("backlog", self.where("task-0028"))

    def test_merge_confirmation_moves_the_subtasks_and_says_so(self):
        self.write("active", "task-0100",
                   f"id: 100\nepic: {EPIC}\numbrella: true\nsubtasks: [task-0028]\ncompleted:")
        self.write("backlog", "task-0028", f"id: 28\nepic: {EPIC}")
        merged = [{"repo": "agentry", "branch": "feature/task-0100", "in_main": True}]
        with unittest.mock.patch.object(advance.git_state, "task_report", return_value=merged), \
                unittest.mock.patch.object(advance, "_merge_wait", side_effect=lambda t, r, w: r), \
                unittest.mock.patch.object(advance, "result") as result:
            advance.finish_or_wait_for_merge("task-0100", {})
        self.assertEqual("done", self.where("task-0100"))
        self.assertEqual("done", self.where("task-0028"))
        self.assertIn("task-0028", result.call_args.args[3])

    def test_a_failing_sweep_does_not_stop_the_done_result(self):
        self.write("active", "task-0100", f"id: 100\nepic: {EPIC}\numbrella: true")
        merged = [{"repo": "agentry", "branch": "feature/task-0100", "in_main": True}]
        with unittest.mock.patch.object(advance.git_state, "task_report", return_value=merged), \
                unittest.mock.patch.object(state, "move_swarm_subtasks", side_effect=OSError), \
                unittest.mock.patch.object(advance, "_merge_wait",
                                           side_effect=lambda t, r, w: r) as wait, \
                unittest.mock.patch.object(advance, "result") as result:
            advance.finish_or_wait_for_merge("task-0100", {})
        wait.assert_called_once()
        self.assertEqual("done", result.call_args.args[0])

    def test_drift_reconciler_sweeps_subtasks_of_a_merged_umbrella(self):
        self.write("active", "task-0100",
                   f"id: 100\nepic: {EPIC}\numbrella: true\nsubtasks: [task-0028]\ncompleted:")
        self.write("backlog", "task-0028", f"id: 28\nepic: {EPIC}")
        self.write("backlog", "task-0030", f"id: 30\nepic: {EPIC}")
        with unittest.mock.patch.object(stop_gate, "ACTIVE_DIR", self.dirs["active"]), \
                unittest.mock.patch.object(stop_gate, "_main_commit_subjects",
                                           return_value="feat: swarm [task-0100]"), \
                unittest.mock.patch.object(state, "all_runs", return_value=[]), \
                unittest.mock.patch.object(state, "set_fields"):
            moved, _ = stop_gate.reconcile_status_drift(object())
        self.assertEqual(["task-0100", "task-0028"], moved)
        self.assertEqual("done", self.where("task-0028"))
        self.assertEqual("backlog", self.where("task-0030"))


if __name__ == "__main__":
    unittest.main()

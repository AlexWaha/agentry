"""task-0121: the handoff and memory debt readers honour an umbrella.

After task-0118 merged, its 15 swarm subtasks were swept to done/ and each one then
read as an undocumented, unstamped done task, blocking every registration until the
User approved a waiver per subtask. `state.covering_umbrella()` names the done
umbrella that documents a subtask, and both readers skip a covered subtask.

Everything here runs on a sandbox copy of the folder layout (tmproot.sandbox); the
live `.agentry` is never read or written. The layout mirrors the real case: one done
umbrella listing 15 done subtasks of the same epic, some with User waiver docs.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parent / "pipeline"
MEMORY_DIR = TESTS_DIR.parent / "memory"
for _dir in (MEMORY_DIR, PIPELINE_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import handoff
import state
import tmproot
import update

EPIC = "epic-0042"
UMBRELLA = "task-9300"
SUBTASKS = [f"task-93{n:02d}" for n in range(1, 16)]
WAIVER = "---\ntask: {task}\nwaived: true\nreason: swept with its umbrella\n---\n"


class UmbrellaDebtTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "umbrella_debt_").resolve()
        self.done = self.root / ".agentry" / "tasks" / "done"
        self.active = self.root / ".agentry" / "tasks" / "active"
        self.backlog = self.root / ".agentry" / "tasks" / "backlog"
        self.handoffs = self.root / ".agentry" / "tasks" / "handoffs"
        self.stamps = self.root / ".agentry" / "state" / "memory"
        for folder in (self.done, self.active, self.backlog, self.handoffs, self.stamps):
            folder.mkdir(parents=True)
        dirs = {"backlog": self.backlog, "active": self.active, "done": self.done}
        self.baseline = ""
        config = lambda: {"enabled": True, "baseline": self.baseline}  # noqa: E731
        for target, name, value in (
                (state, "ROOT", self.root), (state, "TASK_DIRS", dirs),
                (state, "STATE_DIR", self.root / ".agentry" / "state"),
                (state, "DB_PATH", self.root / ".agentry" / "state" / "run.db"),
                (update, "DONE_DIR", self.done), (update, "STAMP_DIR", self.stamps)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for module in (handoff, update):
            patcher = unittest.mock.patch.object(module, "cfg", side_effect=config)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- fixtures ----------------------------------------------------------------

    def put(self, folder: Path, task: str, head: str = "") -> None:
        (folder / f"{task}.md").write_text(f"---\nid: 1\n{head}\n---\nbody\n", encoding="utf-8")

    def umbrella(self, folder: Path | None = None, epic: str = EPIC, flag: str = "true",
                 listed: list[str] | None = None) -> None:
        listed = SUBTASKS if listed is None else listed
        self.put(folder or self.done, UMBRELLA,
                 f"epic: {epic}\numbrella: {flag}\nsubtasks: [{', '.join(listed)}]")
        # The umbrella flag, epic and list are read from the run row (task-0124), and
        # only a row at `done` with the commit approved covers anything.
        conn = state.connect()
        try:
            if state.get_run(conn, UMBRELLA) is None:
                state.create_run(conn, UMBRELLA, "feature", "done")
                state.set_fields(conn, UMBRELLA, commit_approved=1)
        finally:
            conn.close()

    def subtasks(self, epic: str = EPIC) -> None:
        for task in SUBTASKS:
            self.put(self.done, task, f"epic: {epic}")

    def document(self, task: str) -> None:
        (self.handoffs / f"{task}.md").write_text(WAIVER.format(task=task), encoding="utf-8")

    def stamp(self, task: str) -> None:
        (self.stamps / f"{task}.json").write_text("{}", encoding="utf-8")

    def handoff_debt(self) -> list[str]:
        return [d["task"] for d in handoff.uncovered_done_tasks()]

    def memory_debt(self) -> list[str]:
        return [d["task"] for d in update.unstamped_done_tasks()]


class CoveringUmbrellaTest(UmbrellaDebtTest):

    def test_a_done_umbrella_of_the_same_epic_listing_the_task_covers_it(self):
        self.umbrella()
        self.subtasks()
        for task in SUBTASKS:
            self.assertEqual(UMBRELLA, state.covering_umbrella(task))

    def test_the_umbrella_does_not_cover_itself(self):
        self.umbrella()
        self.assertIsNone(state.covering_umbrella(UMBRELLA))

    def test_a_task_the_umbrella_does_not_list_is_not_covered(self):
        self.umbrella(listed=SUBTASKS[:3])
        self.subtasks()
        self.assertEqual(UMBRELLA, state.covering_umbrella(SUBTASKS[0]))
        self.assertIsNone(state.covering_umbrella(SUBTASKS[3]))

    def test_a_listed_task_of_another_epic_is_not_covered(self):
        self.umbrella()
        self.subtasks(epic="epic-0099")
        self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))

    def test_a_task_with_no_epic_is_not_covered(self):
        self.umbrella()
        self.put(self.done, SUBTASKS[0], "")
        self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))

    def test_an_umbrella_that_is_not_in_done_covers_nothing(self):
        self.subtasks()
        for folder in (self.active, self.backlog):
            with self.subTest(folder=folder.name):
                self.umbrella(folder)
                self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))
                (folder / f"{UMBRELLA}.md").unlink()

    def test_a_done_copy_shadowed_by_a_live_file_of_the_same_id_covers_nothing(self):
        self.subtasks()
        self.umbrella()
        self.umbrella(self.active)
        self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))

    def test_a_file_that_is_not_flagged_as_an_umbrella_covers_nothing(self):
        self.umbrella(flag="false")
        self.subtasks()
        self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))

    def test_an_unreadable_done_folder_answers_none(self):
        self.umbrella()
        self.subtasks()
        with unittest.mock.patch.object(state, "_head_field", side_effect=OSError("boom")):
            self.assertIsNone(state.covering_umbrella(SUBTASKS[0]))


class HandoffDebtTest(UmbrellaDebtTest):

    def test_subtasks_with_no_doc_are_covered_by_a_documented_umbrella(self):
        self.umbrella()
        self.subtasks()
        self.document(UMBRELLA)
        self.assertEqual([], self.handoff_debt())

    def test_the_live_case_fifteen_waivers_and_an_umbrella_with_no_doc_owes_only_the_umbrella(self):
        self.umbrella()
        self.subtasks()
        for task in SUBTASKS:
            self.document(task)
        self.assertEqual([UMBRELLA], self.handoff_debt())

    def test_without_any_doc_only_the_umbrella_is_owed_not_its_fifteen_subtasks(self):
        self.umbrella()
        self.subtasks()
        self.assertEqual([UMBRELLA], self.handoff_debt())

    def test_an_unlisted_subtask_of_the_epic_is_still_owed(self):
        self.umbrella(listed=SUBTASKS[:-1])
        self.subtasks()
        self.document(UMBRELLA)
        self.assertEqual([SUBTASKS[-1]], self.handoff_debt())

    def test_a_subtask_of_another_epic_is_still_owed(self):
        self.umbrella()
        self.subtasks()
        self.put(self.done, SUBTASKS[0], "epic: epic-0099")
        self.document(UMBRELLA)
        self.assertEqual([SUBTASKS[0]], self.handoff_debt())

    def test_a_queued_umbrella_does_not_cancel_the_subtasks_debt(self):
        self.umbrella(self.backlog)
        self.subtasks()
        self.assertEqual(SUBTASKS, self.handoff_debt())

    def test_a_grandfathered_umbrella_does_not_hide_a_subtask_above_the_baseline(self):
        self.baseline = UMBRELLA
        self.umbrella()
        self.subtasks()
        self.assertEqual(SUBTASKS, self.handoff_debt())

    def test_an_ordinary_done_task_without_a_doc_is_still_owed(self):
        self.put(self.done, "task-9400", "epic: epic-0001")
        self.assertEqual(["task-9400"], self.handoff_debt())


class MemoryDebtTest(UmbrellaDebtTest):

    def test_subtasks_with_no_stamp_are_covered_by_a_stamped_umbrella(self):
        self.umbrella()
        self.subtasks()
        self.stamp(UMBRELLA)
        self.assertEqual([], self.memory_debt())

    def test_without_any_stamp_only_the_umbrella_is_owed_not_its_fifteen_subtasks(self):
        self.umbrella()
        self.subtasks()
        self.assertEqual([UMBRELLA], self.memory_debt())

    def test_an_unlisted_subtask_of_the_epic_is_still_owed(self):
        self.umbrella(listed=SUBTASKS[:-1])
        self.subtasks()
        self.stamp(UMBRELLA)
        self.assertEqual([SUBTASKS[-1]], self.memory_debt())

    def test_a_queued_umbrella_does_not_cancel_the_subtasks_debt(self):
        self.umbrella(self.active)
        self.subtasks()
        self.assertEqual(SUBTASKS, self.memory_debt())

    def test_a_grandfathered_umbrella_does_not_hide_a_subtask_above_the_baseline(self):
        self.baseline = UMBRELLA
        self.umbrella()
        self.subtasks()
        self.assertEqual(SUBTASKS, self.memory_debt())

    def test_an_ordinary_done_task_without_a_stamp_is_still_owed(self):
        self.put(self.done, "task-9400", "epic: epic-0001")
        self.assertEqual(["task-9400"], self.memory_debt())


if __name__ == "__main__":
    unittest.main()

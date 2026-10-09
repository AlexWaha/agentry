"""The push checkpoint is CEO-gated at every approval level.

rules/git-workflow.md ("Push is never automatic, at any approval level") said so
while GRANTS[AUTO] still contained PUSH - the rule was written and not enforced,
and the orchestrator followed the permissive document. These tests pin the
enforcement so the two cannot drift apart again.

The level table now follows the canonical one (auto grants the push of the work
branch) and the withholding moved from a hardcoded set to pipeline.json
`approvals_never_granted`, so the pins read: the shipped config withholds the
push at every level, a missing or unreadable config withholds it too, and only
an explicit empty list lets `auto` push.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import approvals
import state
import tmproot

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = {"approvals_never_granted": ["push", "trunk_push"]}


def config(value):
    """Patch the pipeline config the module reads, nothing else."""
    return unittest.mock.patch.object(state, "load_pipeline", return_value=value)


class PushIsNeverGrantedTest(unittest.TestCase):

    def setUp(self):
        patcher = config(SHIPPED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_level_effectively_grants_push(self):
        for level in approvals.LEVELS:
            with self.subTest(level=level):
                self.assertNotIn(
                    approvals.PUSH, approvals.GRANTS[level] - approvals.never_granted())

    def test_granted_refuses_push_at_every_level(self):
        original = approvals.read
        for level in approvals.LEVELS:
            approvals.read = lambda level=level: level
            try:
                with self.subTest(level=level):
                    self.assertFalse(approvals.granted(approvals.PUSH))
            finally:
                approvals.read = original

    def test_stage_auto_approve_cannot_grant_push(self):
        original = approvals.read
        approvals.read = lambda: approvals.AUTO
        try:
            self.assertFalse(approvals.granted(approvals.PUSH, ["commit", "push"]))
            # Control: the same list still grants a checkpoint that is grantable.
            self.assertTrue(approvals.granted(approvals.COMMIT, ["commit", "push"]))
        finally:
            approvals.read = original

    def test_auto_still_grants_everything_below_the_push(self):
        original = approvals.read
        approvals.read = lambda: approvals.AUTO
        try:
            # Derived from the data, never re-typed: a hardcoded list kept
            # asserting a checkpoint that had already been removed from GRANTS,
            # which is the same defect as one config key protecting one name.
            below_the_push = approvals.GRANTS[approvals.AUTO] - approvals.never_granted()
            self.assertTrue(below_the_push)  # control
            self.assertNotIn(approvals.PUSH, below_the_push)
            for checkpoint in sorted(below_the_push):
                with self.subTest(checkpoint=checkpoint):
                    self.assertTrue(approvals.granted(checkpoint))
        finally:
            approvals.read = original

    def test_descriptions_do_not_promise_the_push(self):
        for level, text in approvals.DESCRIPTIONS.items():
            with self.subTest(level=level):
                self.assertNotIn("through the push", text)


class PublishingCheckpointsAreNeverGrantedTest(unittest.TestCase):
    """PUSH and TRUNK_PUSH (task-0092) are refused at every level, by the level
    and by a stage `auto_approve` list alike."""

    def setUp(self):
        patcher = config(SHIPPED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_both_are_withheld_and_the_trunk_push_is_in_no_level_table(self):
        for level in approvals.LEVELS:
            for checkpoint in (approvals.PUSH, approvals.TRUNK_PUSH):
                with self.subTest(level=level, checkpoint=checkpoint):
                    self.assertIn(checkpoint, approvals.never_granted())
            with self.subTest(level=level, checkpoint=approvals.TRUNK_PUSH):
                self.assertNotIn(approvals.TRUNK_PUSH, approvals.GRANTS[level])

    def test_granted_refuses_both_at_every_level_even_when_the_stage_lists_them(self):
        for level in approvals.LEVELS:
            for checkpoint in (approvals.PUSH, approvals.TRUNK_PUSH):
                with self.subTest(level=level, checkpoint=checkpoint):
                    with unittest.mock.patch.object(approvals, "read", return_value=level):
                        self.assertFalse(approvals.granted(checkpoint))
                        self.assertFalse(approvals.granted(checkpoint, [checkpoint]))


class AssistedAndAutoDifferTest(unittest.TestCase):
    """task-0050: the two levels shared one grant set, so choosing `auto` for a
    night run changed nothing. Only `auto` takes the next ready task."""

    def test_the_grant_sets_differ(self):
        self.assertNotEqual(approvals.GRANTS[approvals.ASSISTED],
                            approvals.GRANTS[approvals.AUTO])

    def test_only_auto_grants_taking_a_task(self):
        expected = {approvals.MANUAL: False, approvals.ASSISTED: False,
                    approvals.AUTO: True}
        for level, take in expected.items():
            with self.subTest(level=level):
                with unittest.mock.patch.object(approvals, "read", return_value=level):
                    self.assertEqual(take, approvals.granted(approvals.TAKE))

    def test_assisted_and_auto_both_grant_the_commit_and_manual_does_not(self):
        expected = {approvals.MANUAL: False, approvals.ASSISTED: True,
                    approvals.AUTO: True}
        for level, commit in expected.items():
            with self.subTest(level=level):
                with unittest.mock.patch.object(approvals, "read", return_value=level):
                    self.assertEqual(commit, approvals.granted(approvals.COMMIT))

    def test_the_description_names_what_distinguishes_each_level(self):
        self.assertIn("next ready task", approvals.DESCRIPTIONS[approvals.AUTO])
        self.assertNotIn("next ready task", approvals.DESCRIPTIONS[approvals.ASSISTED])
        self.assertIn("you start each task", approvals.DESCRIPTIONS[approvals.ASSISTED])


class GrantsPerLevelTest(unittest.TestCase):
    """The default grants, with nothing withheld by config. `trunk_push` and
    `plan` stay out even then: they are withheld in code."""

    def granted_at(self, level, checkpoint, stage_auto=None):
        with config({"approvals_never_granted": []}), \
                unittest.mock.patch.object(approvals, "read", return_value=level):
            return approvals.granted(checkpoint, stage_auto)

    def test_manual_grants_nothing(self):
        for checkpoint in (approvals.TAKE, approvals.COMMIT, approvals.PUSH):
            with self.subTest(checkpoint=checkpoint):
                self.assertFalse(self.granted_at(approvals.MANUAL, checkpoint))

    def test_assisted_grants_the_commit_but_not_taking_a_task_or_the_push(self):
        # assisted grants the commit only; `take` is granted at auto only (task-0050).
        self.assertTrue(self.granted_at(approvals.ASSISTED, approvals.COMMIT))
        self.assertFalse(self.granted_at(approvals.ASSISTED, approvals.TAKE))
        self.assertFalse(self.granted_at(approvals.ASSISTED, approvals.PUSH))

    def test_auto_grants_take_commit_and_the_push_of_the_work_branch(self):
        for checkpoint in (approvals.TAKE, approvals.COMMIT, approvals.PUSH):
            with self.subTest(checkpoint=checkpoint):
                self.assertTrue(self.granted_at(approvals.AUTO, checkpoint))

    def test_trunk_push_and_plan_are_never_granted_whatever_the_config_and_the_stage_say(self):
        for level in approvals.LEVELS:
            for checkpoint in (approvals.TRUNK_PUSH, approvals.PLAN):
                with self.subTest(level=level, checkpoint=checkpoint):
                    self.assertFalse(self.granted_at(level, checkpoint, [checkpoint]))


class ConfigWithholdsGrantsTest(unittest.TestCase):
    """`approvals_never_granted` withholds a grant from every level and from a
    stage `auto_approve` list; it only ever adds to what is withheld."""

    def granted_at(self, level, checkpoint, listed, stage_auto=None):
        with config({"approvals_never_granted": listed}), \
                unittest.mock.patch.object(approvals, "read", return_value=level):
            return approvals.granted(checkpoint, stage_auto)

    def test_listing_the_push_withholds_it_at_auto(self):
        self.assertTrue(self.granted_at(approvals.AUTO, approvals.PUSH, []))
        self.assertFalse(self.granted_at(approvals.AUTO, approvals.PUSH, ["push"]))

    def test_listing_a_checkpoint_withholds_it_from_the_stage_list_too(self):
        self.assertTrue(self.granted_at(approvals.MANUAL, approvals.COMMIT, [], ["commit"]))
        self.assertFalse(self.granted_at(approvals.MANUAL, approvals.COMMIT, ["commit"], ["commit"]))

    def test_any_grant_can_be_withheld_not_only_the_push(self):
        self.assertFalse(self.granted_at(approvals.AUTO, approvals.TAKE, ["take"]))
        self.assertTrue(self.granted_at(approvals.AUTO, approvals.COMMIT, ["take"]))

    def test_the_withheld_set_is_the_code_floor_plus_the_list(self):
        with config({"approvals_never_granted": ["commit"]}):
            self.assertEqual({approvals.TRUNK_PUSH, approvals.PLAN, approvals.COMMIT},
                             set(approvals.never_granted()))


class MissingConfigFailsSafeTest(unittest.TestCase):
    """No usable list withholds the push and the trunk push, never grants more."""

    EXPECTED = frozenset({approvals.PUSH, approvals.TRUNK_PUSH, approvals.PLAN})

    def assert_push_withheld_at_auto(self):
        self.assertEqual(self.EXPECTED, set(approvals.never_granted()))
        with unittest.mock.patch.object(approvals, "read", return_value=approvals.AUTO):
            self.assertFalse(approvals.granted(approvals.PUSH))
            self.assertFalse(approvals.granted(approvals.PUSH, ["push"]))
            self.assertTrue(approvals.granted(approvals.COMMIT))   # control

    def test_a_config_without_the_key(self):
        with config({"main_branch": "main"}):
            self.assert_push_withheld_at_auto()

    def test_an_empty_config(self):
        with config({}):
            self.assert_push_withheld_at_auto()

    def test_a_value_that_is_not_a_list(self):
        for junk in (None, "push", True, 0, {"push": True}):
            with self.subTest(value=junk), config({"approvals_never_granted": junk}):
                self.assert_push_withheld_at_auto()

    def test_a_list_with_an_entry_that_is_not_exactly_one_checkpoint_name(self):
        # A typo or a malformed entry must not be read as "withhold nothing extra":
        # the whole list is discarded and the strict default applies instead.
        for junk in (["push,trunk_push"], ["psuh"], [None], [""], [["push"]],
                     [" PUSH "], ["commit", "psuh"]):
            with self.subTest(value=junk), config({"approvals_never_granted": junk}):
                self.assert_push_withheld_at_auto()

    def test_a_config_file_that_is_not_a_json_object(self):
        # state.load_pipeline() returns {} for these, so never_granted() fails safe.
        for text in ("[]", '"x"', "0", "null"):
            cfg = tmproot.sandbox(self, "approvals_cfg_") / "pipeline.json"
            cfg.write_text(text, encoding="utf-8")
            with self.subTest(text=text), \
                    unittest.mock.patch.object(state, "PIPELINE_PATH", cfg):
                self.assertEqual({}, state.load_pipeline())
                self.assert_push_withheld_at_auto()

    def test_a_deeply_nested_config_fails_safe(self):
        # json.loads raises RecursionError (not ValueError) on absurd nesting.
        deep = tmproot.sandbox(self, "approvals_cfg_") / "pipeline.json"
        deep.write_text("[" * 100000, encoding="utf-8")
        with unittest.mock.patch.object(state, "PIPELINE_PATH", deep), \
                unittest.mock.patch.object(approvals, "read", return_value=approvals.AUTO):
            self.assertEqual({}, state.load_pipeline())
            self.assertFalse(approvals.granted(approvals.PUSH))
            self.assert_push_withheld_at_auto()

    def test_a_missing_pipeline_file(self):
        gone = tmproot.sandbox(self, "approvals_cfg_") / "absent.json"
        with unittest.mock.patch.object(state, "PIPELINE_PATH", gone):
            self.assert_push_withheld_at_auto()

    def test_a_pipeline_file_that_is_not_json(self):
        broken = tmproot.sandbox(self, "approvals_cfg_") / "pipeline.json"
        broken.write_text('{"approvals_never_granted": [', encoding="utf-8")
        with unittest.mock.patch.object(state, "PIPELINE_PATH", broken):
            self.assert_push_withheld_at_auto()

    def test_a_real_pipeline_file_with_an_explicit_empty_list_is_the_one_way_to_open_the_push(self):
        opened = tmproot.sandbox(self, "approvals_cfg_") / "pipeline.json"
        opened.write_text('{"approvals_never_granted": []}', encoding="utf-8")
        with unittest.mock.patch.object(state, "PIPELINE_PATH", opened), \
                unittest.mock.patch.object(approvals, "read", return_value=approvals.AUTO):
            self.assertTrue(approvals.granted(approvals.PUSH))
            self.assertFalse(approvals.granted(approvals.TRUNK_PUSH))


class ShippedConfigWithholdsThePushTest(unittest.TestCase):
    """The files this project actually ships, not a fixture: behaviour stays
    "the push is never automatic" only while both of them say so."""

    def keys(self, path):
        return json.loads(path.read_text(encoding="utf-8")).get("approvals_never_granted")

    def test_the_repository_pipeline_json_lists_push_and_trunk_push(self):
        self.assertEqual(["push", "trunk_push"],
                         self.keys(PROJECT_ROOT / ".agentry" / "pipeline.json"))

    @unittest.skipUnless((PROJECT_ROOT.parent / ".agentry" / "pipeline.json").is_file(),
                         "no workspace root above this repository")
    def test_the_workspace_root_pipeline_json_lists_push_and_trunk_push(self):
        self.assertEqual(["push", "trunk_push"],
                         self.keys(PROJECT_ROOT.parent / ".agentry" / "pipeline.json"))

    def test_the_real_config_withholds_the_push_at_auto(self):
        with unittest.mock.patch.object(approvals, "read", return_value=approvals.AUTO):
            self.assertFalse(approvals.granted(approvals.PUSH))
            self.assertTrue(approvals.granted(approvals.TAKE))


if __name__ == "__main__":
    unittest.main()

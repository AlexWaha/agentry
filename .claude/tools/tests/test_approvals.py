"""The push checkpoint is CEO-gated at every approval level.

rules/git-workflow.md ("Push is never automatic, at any approval level") said so
while GRANTS[AUTO] still contained PUSH - the rule was written and not enforced,
and the orchestrator followed the permissive document. These tests pin the
enforcement so the two cannot drift apart again.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import approvals


class PushIsNeverGrantedTest(unittest.TestCase):

    def test_no_level_grants_push(self):
        for level in approvals.LEVELS:
            with self.subTest(level=level):
                self.assertNotIn(approvals.PUSH, approvals.GRANTS[level])

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
            self.assertTrue(approvals.GRANTS[approvals.AUTO])  # control
            for checkpoint in sorted(approvals.GRANTS[approvals.AUTO]):
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

    def test_neither_is_in_any_level_grant_set(self):
        for level in approvals.LEVELS:
            for checkpoint in (approvals.PUSH, approvals.TRUNK_PUSH):
                with self.subTest(level=level, checkpoint=checkpoint):
                    self.assertIn(checkpoint, approvals.NEVER_GRANTED)
                    self.assertNotIn(checkpoint, approvals.GRANTS[level])

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


if __name__ == "__main__":
    unittest.main()

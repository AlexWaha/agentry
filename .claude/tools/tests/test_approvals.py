"""The push checkpoint is CEO-gated at every approval level.

rules/git-workflow.md ("Push is never automatic, at any approval level") said so
while GRANTS[AUTO] still contained PUSH - the rule was written and not enforced,
and the orchestrator followed the permissive document. These tests pin the
enforcement so the two cannot drift apart again.
"""

from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()

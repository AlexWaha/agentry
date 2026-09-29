"""task-0004, FR-5 check_glob(): the artifact gate against any path pattern.
"""

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

import artifact_gate
import state
import tmproot


class ArtifactGlobTest(unittest.TestCase):
    """FR-5 (artifact_gate row): the same two checks - names the task, carries
    real content - against any path pattern, for a project whose planning
    artifact is neither a plan nor a spec."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "artifact_glob_")
        (self.root / "docs").mkdir()
        p = unittest.mock.patch.object(state, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, name, chars):
        (self.root / "docs" / name).write_text("x" * chars, encoding="utf-8")

    def test_a_substantial_file_naming_the_task_passes(self):
        self._write("design-task-0100.md", artifact_gate.MIN_CHARS)
        ok, message = artifact_gate.check_glob("task-0100", "docs/*.md", "design")
        self.assertTrue(ok)
        self.assertIn("design-task-0100.md", message)

    def test_a_file_not_naming_the_task_does_not_pass(self):
        self._write("design-task-0999.md", artifact_gate.MIN_CHARS)
        ok, message = artifact_gate.check_glob("task-0100", "docs/*.md", "design")
        self.assertFalse(ok)
        self.assertIn("docs/*.md", message)

    def test_a_heading_and_a_promise_does_not_pass(self):
        self._write("design-task-0100.md", artifact_gate.MIN_CHARS - 1)
        ok, _ = artifact_gate.check_glob("task-0100", "docs/*.md", "design")
        self.assertFalse(ok)

    def test_the_four_named_kinds_still_work(self):
        # The glob form is an addition, not a replacement.
        ok, message = artifact_gate.check("task-0100", "spec")
        self.assertFalse(ok)
        self.assertIn("no spec for task-0100", message)


if __name__ == "__main__":
    unittest.main()

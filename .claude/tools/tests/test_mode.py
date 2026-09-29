"""The set of workflow modes is read from pipeline.json (mode.py).
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

import state
import tmproot


class ModeSetFromConfigTest(unittest.TestCase):
    """The set of modes is a property of the project: everything except `talk`
    comes from pipeline.json, so a workspace declaring a `research` flow gets a
    `research` mode without editing this file."""

    def setUp(self):
        import mode
        self.mode = mode
        # A real file rather than a patched Path method: a Path instance's
        # read_text is read-only and cannot be mocked.
        self.tmp = tmproot.sandbox(self, "mode_file_")
        p = unittest.mock.patch.object(mode, "MODE_PATH", self.tmp / "mode")
        p.start()
        self.addCleanup(p.stop)

    def _stored(self, value):
        (self.tmp / "mode").write_text(value + "\n", encoding="utf-8")

    def _config(self, pipelines):
        return unittest.mock.patch.object(
            state, "load_pipeline", return_value={"pipelines": pipelines})

    def test_declared_flows_become_modes(self):
        with self._config({"build": {}, "research": {}}):
            self.assertEqual(("build", "research", "talk"), self.mode.modes())

    def test_talk_exists_even_when_undeclared(self):
        with self._config({"build": {}}):
            self.assertIn("talk", self.mode.modes())

    def test_an_empty_config_falls_back_to_the_three_names(self):
        with self._config({}):
            self.assertEqual(self.mode.MODES, self.mode.modes())

    def test_the_default_is_build_whatever_the_key_order(self):
        # Not "the first key": JSON key order is not a decision, and reordering
        # pipelines.json would have moved every unmarked task to another flow.
        for pipelines in ({"research": {}, "build": {}}, {"build": {}, "research": {}}):
            with self.subTest(pipelines=list(pipelines)), self._config(pipelines):
                self.assertEqual("build", self.mode.default())

    def test_without_build_the_default_is_the_first_declared_flow(self):
        with self._config({"research": {}, "design": {}}):
            self.assertEqual("research", self.mode.default())

    def test_describe_names_the_real_stages(self):
        with self._config({"build": {"stages": [{"name": "implement"},
                                                {"name": "test"}]}}):
            self.assertEqual("implementation flow: implement, test",
                             self.mode.describe("build"))

    def test_describe_falls_back_for_an_unknown_name(self):
        with self._config({"research": {}}):
            self.assertEqual("research flow", self.mode.describe("research"))

    def test_an_unknown_stored_mode_resolves_to_the_default(self):
        self._stored("nonsense")
        with self._config({"build": {}, "plan": {}}):
            self.assertEqual("build", self.mode.read())

    def test_a_missing_mode_file_resolves_to_the_default(self):
        with self._config({"research": {}, "design": {}}):
            self.assertEqual("research", self.mode.read())

    def test_a_declared_flow_drives_the_conveyor(self):
        self._stored("research")
        with self._config({"research": {}}):
            self.assertEqual("research", self.mode.read())
            self.assertTrue(self.mode.conveyor_runs())

    def test_talk_does_not_drive_the_conveyor(self):
        self._stored("talk")
        with self._config({"build": {}}):
            self.assertFalse(self.mode.conveyor_runs())


if __name__ == "__main__":
    unittest.main()

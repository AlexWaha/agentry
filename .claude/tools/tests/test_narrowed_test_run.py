"""agent_gate.is_narrowed(): a dev agent may run a NARROWED suite, never the full
one - two tests shipped red under the strict split.
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

import agent_gate


class NarrowedTestRunTest(unittest.TestCase):
    """A dev agent may run a NARROWED suite, never the full one.

    Proving a new test actually bites means running it with the production line
    removed; two tests shipped red under the strict deny. The unfiltered run
    stays denied, so the one-full-run-per-cycle budget still belongs to the
    orchestrator."""

    def _gates(self, cfg):
        return unittest.mock.patch.object(agent_gate.pretool_gate, "gates_cfg",
                                          return_value=cfg)

    def test_a_filtered_run_is_narrowed(self):
        with self._gates({}):
            self.assertTrue(agent_gate.is_narrowed("vendor/bin/pest --filter=Order"))
            self.assertTrue(agent_gate.is_narrowed("pytest -k order_total"))

    def test_a_bare_suite_run_is_not_narrowed(self):
        with self._gates({}):
            self.assertFalse(agent_gate.is_narrowed("vendor/bin/pest"))
            self.assertFalse(agent_gate.is_narrowed("npm run test"))

    def test_a_flag_glued_into_a_word_does_not_count(self):
        with self._gates({}):
            self.assertFalse(agent_gate.is_narrowed("run--filter-suite"))

    def test_a_flag_inside_a_comment_does_not_count(self):
        # A substring test read the commented flag as narrowing, so a FULL suite
        # run passed the dev-forbidden-commands gate.
        with self._gates({}):
            self.assertFalse(agent_gate.is_narrowed("pytest  # -k nothing"))
            self.assertFalse(agent_gate.is_narrowed("pytest # --filter=Order"))

    def test_a_commented_flag_does_not_buy_a_full_run(self):
        cfg = {"dev_forbidden_commands": ["pytest"]}
        with self._gates(cfg):
            self.assertEqual("pytest", agent_gate.unnarrowed_test_cmd("pytest  # -k nothing"))

    def test_the_flag_list_is_config_driven(self):
        with self._gates({"dev_test_filter_flags": ["--only"]}):
            self.assertTrue(agent_gate.is_narrowed("suite --only Order"))
            self.assertFalse(agent_gate.is_narrowed("suite --filter Order"))

    def test_a_chain_is_judged_per_segment(self):
        # The flag narrows the segment it sits in, not the whole string: a bare
        # second run rode in on the first run's filter.
        cfg = {"dev_forbidden_commands": ["pytest"]}
        with self._gates(cfg):
            self.assertEqual("pytest", agent_gate.unnarrowed_test_cmd("pytest -k x; pytest"))
            self.assertEqual("pytest", agent_gate.unnarrowed_test_cmd("pytest && pytest -k x"))
            self.assertEqual("", agent_gate.unnarrowed_test_cmd("pytest -k x && pytest -k y"))
            self.assertEqual("", agent_gate.unnarrowed_test_cmd("ruff check && pytest -k x"))

    def test_a_chain_hiding_a_full_run_is_denied(self):
        cfg = {"dev_forbidden_commands": ["npm run test"]}
        with self._gates(cfg):
            self.assertEqual(2, agent_gate.handle_dev(
                "Bash", {"command": "npm run test -- -k cart; npm run test"}))

    def test_the_full_suite_is_denied_and_a_narrowed_one_is_not(self):
        cfg = {"dev_forbidden_commands": ["npm run test"]}
        with self._gates(cfg), unittest.mock.patch.object(
                agent_gate.pretool_gate, "handle_bash", return_value=0) as passthru:
            self.assertEqual(2, agent_gate.handle_dev(
                "Bash", {"command": "npm run test"}))
            passthru.assert_not_called()
            self.assertEqual(0, agent_gate.handle_dev(
                "Bash", {"command": "npm run test -- -k cart"}))
            passthru.assert_called_once()


if __name__ == "__main__":
    unittest.main()

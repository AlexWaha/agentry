"""task-0119 (M5): the per-repo gate commands and the exit gates cannot drift.

`repos.<key>.stages.implement|review` (read by stack_gate.py) and the build
pipeline's `exit_gate.cmd` for the same stage (read by advance.py) are two
spellings of one gate. Nothing wires one to the other, so this pins them equal on
the real pipeline.json. `test` is excluded on purpose: its exit gate is the
full suite plus the UI evidence check, and the repo entry leaves it empty.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / ".agentry" / "pipeline.json"
PARITY_STAGES = ("implement", "review")


class StackGateParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))

    def exit_cmd(self, stage: str) -> str:
        for entry in self.config["pipelines"]["build"]["stages"]:
            if entry.get("name") == stage:
                return entry["exit_gate"]["cmd"]
        self.fail(f"the build pipeline has no '{stage}' stage")

    def test_every_repo_entry_matches_the_exit_gate_of_its_stages(self):
        repos = self.config["repos"]
        self.assertTrue(repos, "repos{} is empty, so there is nothing to compare")
        for key, entry in repos.items():
            for stage in PARITY_STAGES:
                with self.subTest(repo=key, stage=stage):
                    self.assertEqual(self.exit_cmd(stage), " && ".join(entry["stages"][stage]))

    def test_the_comparison_is_not_vacuous(self):
        for stage in PARITY_STAGES:
            with self.subTest(stage=stage):
                self.assertTrue(self.exit_cmd(stage).strip())


if __name__ == "__main__":
    unittest.main()

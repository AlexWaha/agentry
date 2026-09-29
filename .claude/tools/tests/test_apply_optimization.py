"""apply_optimization.py must never turn a model family alias into a pinned id.

task-0093: the routing rule (token-policy.md "Routing") lives on the alias
(`sonnet` by default, `opus` for architect). `apply_optimization.py` used to
carry a `MODEL_PINS` map that rewrote `model: sonnet` to `model: claude-sonnet-5`
at fleet-rollout time - exactly the kind of pin the routing rule forbids. This
test proves the rewrite is gone and stays gone: it runs the real
`patch_agents()` function against a scratch copy of this repo's own 31 agent
files (never the live `.claude/agents/`) and asserts nothing pinned comes out
the other side, and that the routing defaults survive the pass unchanged.
"""

from __future__ import annotations

import re
import shutil
import sys
import unittest
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parents[1] / "setup"
TESTS_DIR = Path(__file__).resolve().parent
AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
sys.path.insert(0, str(SETUP_DIR))
sys.path.insert(0, str(TESTS_DIR))

import apply_optimization  # noqa: E402
import tmproot  # noqa: E402

# Any `claude-<family>-<version>` shaped id, pinned or not yet invented. The
# test must catch a successor pin scheme, not just today's two exact strings.
PINNED_ID_RE = re.compile(r"claude-[a-z]+-\d")


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}: *(\S+)$", text, re.MULTILINE)
    return match.group(1) if match else None


class ApplyOptimizationModelAliasTest(unittest.TestCase):

    def setUp(self) -> None:
        sandbox = tmproot.sandbox(self, "apply_optimization_agents_")
        dst_claude = sandbox / ".claude"
        dst_agents = dst_claude / "agents"
        dst_agents.mkdir(parents=True)
        self.before = {}
        for path in sorted(AGENTS_DIR.glob("*.md")):
            shutil.copy2(path, dst_agents / path.name)
            self.before[path.stem] = _scalar(path.read_text(encoding="utf-8"), "model")
        self.dst_claude = dst_claude
        self.target_root = sandbox

    def test_no_pinned_model_id_after_patch_agents(self):
        report: dict = {}
        apply_optimization.patch_agents(self.dst_claude, self.target_root, report)

        for path in sorted((self.dst_claude / "agents").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(agent=path.stem):
                self.assertNotIn("claude-opus-5", text)
                self.assertNotIn("claude-sonnet-5", text)
                self.assertNotRegex(text, PINNED_ID_RE)

    def test_routing_defaults_survive_the_pass(self):
        report: dict = {}
        apply_optimization.patch_agents(self.dst_claude, self.target_root, report)

        after = {
            path.stem: _scalar(path.read_text(encoding="utf-8"), "model")
            for path in (self.dst_claude / "agents").glob("*.md")
        }
        self.assertEqual(after, self.before)
        self.assertEqual(after["architect"], "opus")
        self.assertEqual(after["qa-engineer"], "sonnet")

    def test_effort_max_no_longer_includes_qa_engineer(self):
        # Regression guard for the drift found while auditing EFFORT_MAX
        # against the Phase 5 plan: qa-engineer's effort is "high", not "max".
        self.assertNotIn("qa-engineer", apply_optimization.EFFORT_MAX)
        self.assertEqual(apply_optimization.EFFORT_MAX,
                          {"reviewer", "security-engineer"})

    def test_no_model_pins_map_reintroduced(self):
        # A successor to MODEL_PINS is exactly the regression this file
        # exists to catch; fail loudly the moment one reappears rather than
        # rely only on the behavioural assertions above.
        self.assertFalse(hasattr(apply_optimization, "MODEL_PINS"))


if __name__ == "__main__":
    unittest.main()

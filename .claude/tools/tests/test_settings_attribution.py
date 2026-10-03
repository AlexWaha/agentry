"""AI-authorship is blocked in two layers: the platform setting and the commit gate.

task-0105. Layer 1 is `attribution` in `.claude/settings.json`, which stops Claude
Code from asking the model for a Co-Authored-By trailer or a PR byline. Layer 2 is
`pretool_gate.check_commit_attribution`, which denies a commit that carries one
anyway. The object form is pinned on purpose: the boolean `false` makes CLI builds
older than 2.1.281 skip the whole settings file.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))

import pretool_gate


class AttributionSettingTest(unittest.TestCase):
    def test_settings_blank_commit_pr_and_session_url_in_object_form(self):
        settings = json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            settings.get("attribution"),
            {"commit": "", "pr": "", "sessionUrl": False},
        )


class CommitTrailerGateTest(unittest.TestCase):
    TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"

    def drive(self, command: str) -> tuple[int, str]:
        """handle_bash() with stderr captured: the real entry point, so removing
        the check from its checks tuple turns these tests red."""
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(command, cwd=".")
        return code, err.getvalue()

    def test_commit_with_co_authored_by_trailer_is_denied(self):
        code, msg = self.drive(f'git commit -m "fix: x" -m "{self.TRAILER}"')
        self.assertEqual(2, code)
        self.assertIn("AI-authorship trailer", msg)

    def test_heredoc_commit_with_trailer_is_denied(self):
        cmd = "\n".join([
            "git commit -m \"$(cat <<'EOF'",
            "fix: x",
            "",
            self.TRAILER,
            "EOF",
            ')"',
        ])
        code, msg = self.drive(cmd)
        self.assertEqual(2, code)
        self.assertIn("AI-authorship trailer", msg)

    def test_trailer_text_outside_a_commit_is_not_denied(self):
        # Not a commit, so it returns before any run-state lookup.
        self.assertEqual(0, self.drive(f'echo "{self.TRAILER}"')[0])


if __name__ == "__main__":
    unittest.main()

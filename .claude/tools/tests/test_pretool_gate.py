"""Unit tests for the pure gate functions in pipeline/pretool_gate.py.

Covers task-0001's C-2 (planning-and-documentation exemption) additions plus
the pre-existing branch/dash gate helpers. Only pure functions are exercised
here - no subprocess/git calls, no filesystem, no network.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import pretool_gate


class MatchesC2Test(unittest.TestCase):
    """task-0001 FR-21/FR-22: the planning-and-documentation path set."""

    def test_claude_plans_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/plans/2026-01-01-foo.md"))

    def test_agentry_specs_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/specs/spec-0001.md"))

    def test_docs_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2("docs/technical/architecture.md"))

    def test_root_markdown_file_matches(self):
        self.assertTrue(pretool_gate.matches_c2("CHANGELOG.md"))

    def test_backslash_path_is_normalized(self):
        # Regression guard: a .md extension would also satisfy the root-markdown
        # branch even without normalization (that regex has no "/" requirement),
        # so this must use a non-.md file to actually isolate the backslash-to-
        # forward-slash normalization instead of accidentally riding the other
        # branch. See qa-engineer's task-0001 test-stage finding.
        self.assertTrue(pretool_gate.matches_c2(".agentry\\tasks\\backlog\\task-0043.txt"))

    def test_agentry_plans_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/plans/2026-01-01-foo.md"))

    def test_claude_specs_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/specs/spec-0001.md"))

    def test_agentry_tasks_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/tasks/backlog/task-0043.md"))

    def test_claude_tasks_path_matches(self):
        self.assertTrue(pretool_gate.matches_c2(".agentry/tasks/backlog/task-0043.md"))

    def test_paths_explicitly_outside_c2_do_not_match(self):
        # Contract C-2's explicit exclusion list (spec Data and API Contracts,
        # and task-0001 Notes) - these must never be swept in by a widened prefix.
        outside = (
            ".claude/tools/pipeline/state.py",
            ".claude/agents/reviewer.md",
            ".claude/skills/write-tests/SKILL.md",
            ".claude/rules/git-workflow.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".agentry/pipeline.json",
            ".agentry/memory/lessons.md",
            ".agentry/project/stack.md",
            ".gitignore",
            "src/app.py",
        )
        for path in outside:
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.matches_c2(path))

    def test_source_file_does_not_match(self):
        self.assertFalse(pretool_gate.matches_c2(".claude/tools/pipeline/state.py"))

    def test_nested_root_like_name_does_not_match(self):
        # A .md file inside a subdirectory is not a "root" markdown file.
        self.assertFalse(pretool_gate.matches_c2("src/README.md"))


class PlanningOnlyCommitTest(unittest.TestCase):
    """task-0001 FR-21/FR-22: all-or-nothing over the staged file list."""

    def test_all_staged_files_match_c2_is_exempt(self):
        with unittest.mock.patch.object(
                pretool_gate, "staged_files",
                return_value=["docs/readme.md", ".agentry/plans/x.md"]):
            self.assertTrue(pretool_gate.planning_only_commit("git commit -m x", "."))

    def test_one_non_c2_file_disqualifies_the_whole_commit(self):
        with unittest.mock.patch.object(
                pretool_gate, "staged_files",
                return_value=["docs/readme.md", "src/app.py"]):
            self.assertFalse(pretool_gate.planning_only_commit("git commit -m x", "."))

    def test_no_staged_files_is_not_exempt(self):
        with unittest.mock.patch.object(pretool_gate, "staged_files", return_value=[]):
            self.assertFalse(pretool_gate.planning_only_commit("git commit -m x", "."))


class IsBookkeepingTest(unittest.TestCase):
    """Lint-fixed in task-0043 (tuple startswith); behavior must be unchanged."""

    def test_dot_claude_path_is_bookkeeping(self):
        self.assertTrue(pretool_gate.is_bookkeeping(".agentry/tasks/backlog/task-0043.md"))

    def test_docs_path_is_bookkeeping(self):
        self.assertTrue(pretool_gate.is_bookkeeping("docs/technical/infrastructure.md"))

    def test_nested_dot_claude_path_is_bookkeeping(self):
        self.assertTrue(pretool_gate.is_bookkeeping("some/repo/.claude/rules/git-workflow.md"))

    def test_source_path_is_not_bookkeeping(self):
        self.assertFalse(pretool_gate.is_bookkeeping("src/app/models/user.py"))


class TaskFromBranchTest(unittest.TestCase):
    def test_extracts_task_id_from_standard_branch(self):
        self.assertEqual(pretool_gate.task_from_branch("feature/task-0043"), "task-0043")

    def test_no_task_id_returns_empty(self):
        self.assertEqual(pretool_gate.task_from_branch("main"), "")


class ValidWorkBranchTest(unittest.TestCase):
    def test_protected_branch_is_valid(self):
        self.assertTrue(pretool_gate.valid_work_branch("main"))

    def test_typed_task_branch_is_valid(self):
        self.assertTrue(pretool_gate.valid_work_branch("bugfix/task-0001"))

    def test_untyped_branch_is_invalid(self):
        self.assertFalse(pretool_gate.valid_work_branch("task-0001"))

    def test_unknown_type_prefix_is_invalid(self):
        self.assertFalse(pretool_gate.valid_work_branch("wip/task-0001"))


class HasForbiddenDashTest(unittest.TestCase):
    # chr() escapes, not literal characters, so this file itself stays free of
    # the em dash / en dash it is testing for - same convention pretool_gate.py
    # uses for EM_EN_DASH.
    def test_em_dash_is_forbidden(self):
        self.assertTrue(pretool_gate.has_forbidden_dash("a" + chr(0x2014) + "b"))

    def test_en_dash_is_forbidden(self):
        self.assertTrue(pretool_gate.has_forbidden_dash("a" + chr(0x2013) + "b"))

    def test_hyphen_minus_is_allowed(self):
        self.assertFalse(pretool_gate.has_forbidden_dash("bugfix/task-0001"))


class MaskQuotedRedirectTest(unittest.TestCase):
    """task-0004 (FR-4 inventory, pretool_gate row): a `>` inside a quoted span
    is not a redirect.

    Observed, not hypothetical: a tool call carrying `>` inside a Python format
    string was denied as shell file-authoring while the FR-4 inventory was being
    reviewed. The mask preserves length, so a quoted TARGET stays detectable
    while a quoted OPERATOR stops matching."""

    def test_quoted_comparison_is_not_a_redirect(self):
        self.assertEqual("", pretool_gate.redirect_write_target(
            'python -c "print(f\'{n >= 2}\')"'))

    def test_quoted_sql_comparison_is_not_a_redirect(self):
        self.assertEqual("", pretool_gate.redirect_write_target(
            "psql -c 'select 1 where total > 5'"))

    def test_a_real_redirect_outside_quotes_is_still_caught(self):
        self.assertEqual(
            "> out.txt", pretool_gate.redirect_write_target("echo 'a > b' > out.txt"))

    def test_a_quoted_target_is_still_caught(self):
        # The mask keeps offsets, so the fragment is read from the real command.
        self.assertEqual(
            '> "out file.txt"',
            pretool_gate.redirect_write_target('echo hi > "out file.txt"'))

    def test_mask_preserves_length(self):
        command = "echo 'a > b' > out.txt"
        self.assertEqual(len(command), len(pretool_gate.mask_quoted(command)))

    def test_descriptor_dup_and_discard_still_pass(self):
        self.assertEqual("", pretool_gate.redirect_write_target("make 2>&1"))
        self.assertEqual("", pretool_gate.redirect_write_target("make 2>NUL"))


class RepoBootstrapOrderingTest(unittest.TestCase):
    """task-0001: repo_bootstrap() must be evaluated before current_branch().

    On a genuinely unborn repo, git plumbing for the current branch is
    unreliable (no HEAD to resolve), so the bootstrap exemption has to
    short-circuit the commit check before current_branch()/task_from_branch()
    ever run. This was the original agent's stated reason for the ordering in
    handle_bash() and it must survive the delete-and-hand-restore intact.
    """

    def _patched(self, **overrides):
        base = {
            "check_branch_creation": lambda *a, **k: 0,
            "check_destructive_and_repl": lambda *a, **k: 0,
            "check_commit_attribution": lambda *a, **k: 0,
        }
        base.update(overrides)
        return unittest.mock.patch.multiple(pretool_gate, **base)

    def test_current_branch_not_called_when_bootstrap_is_true(self):
        with self._patched(repo_bootstrap=lambda *a, **k: True), \
                unittest.mock.patch.object(pretool_gate, "current_branch") as mocked:
            code = pretool_gate.handle_bash("git commit -m x", cwd=".")
        self.assertEqual(code, 0)
        mocked.assert_not_called()

    def test_current_branch_is_called_when_bootstrap_is_false(self):
        with self._patched(
                repo_bootstrap=lambda *a, **k: False,
                planning_only_commit=lambda *a, **k: True), \
                unittest.mock.patch.object(pretool_gate, "current_branch",
                                            return_value="main") as mocked:
            pretool_gate.handle_bash("git commit -m x", cwd=".")
        mocked.assert_called_once()


class MainFailOpenTest(unittest.TestCase):
    """NFR-4: an internal error in the gate allows the action rather than
    bricking the session. Forces a real exception inside main()'s dispatch and
    confirms the outer try/except still returns 0 (allow)."""

    @staticmethod
    def _stdin(payload_json: str):
        import io
        return unittest.mock.patch("sys.stdin", io.StringIO(payload_json))

    def test_exception_in_handle_bash_fails_open(self):
        import json
        payload = json.dumps({"tool_name": "Bash",
                               "tool_input": {"command": "git commit -m x"}})
        with self._stdin(payload), \
                unittest.mock.patch.object(pretool_gate, "handle_bash",
                                            side_effect=RuntimeError("boom")):
            self.assertEqual(pretool_gate.main(), 0)

    def test_exception_in_handle_edit_fails_open(self):
        import json
        payload = json.dumps({"tool_name": "Write",
                               "tool_input": {"file_path": "x.py", "content": "y"}})
        with self._stdin(payload), \
                unittest.mock.patch.object(pretool_gate, "handle_edit",
                                            side_effect=RuntimeError("boom")):
            self.assertEqual(pretool_gate.main(), 0)

    def test_malformed_stdin_json_fails_open(self):
        with self._stdin("not valid json"):
            self.assertEqual(pretool_gate.main(), 0)

    def test_unreadable_stdin_fails_open(self):
        # json.loads, not json.load: the gate now reads stdin's raw bytes and
        # decodes UTF-8 itself, so patching the old entry point asserted nothing.
        with unittest.mock.patch("json.loads", side_effect=OSError("boom")):
            self.assertEqual(pretool_gate.main(), 0)


if __name__ == "__main__":
    unittest.main()

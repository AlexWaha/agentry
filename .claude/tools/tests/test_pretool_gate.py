"""Unit tests for the pure gate functions in pipeline/pretool_gate.py.

Covers task-0001's C-2 (planning-and-documentation exemption) additions plus
the pre-existing branch/dash gate helpers. Only pure functions are exercised
here - no subprocess/git calls, no filesystem, no network.
"""

from __future__ import annotations

import inspect
import io
import re
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


class OrchestratorDenyMessageTest(unittest.TestCase):
    """The message must name every tree the predicate accepts.

    orch_allowed_path() accepted .agentry/ from the FR-13 move onward while the
    deny message still listed only .claude/ - so the gate behaved correctly and
    then told the denied orchestrator the wrong place to write instead.

    The earlier version of this test hardcoded its own prefix tuple, which
    pinned today's three trees instead of the invariant: a FOURTH tree added to
    the predicate passed, because the test never asked the predicate what it
    accepts. Both assertions below now read pretool_gate.ORCH_ALLOW_PREFIXES -
    the single definition the predicate iterates and the message formats - and
    the second test denies the predicate any prefix of its own."""

    def deny_message(self) -> str:
        buf = io.StringIO()
        with unittest.mock.patch.object(
                pretool_gate, "orch_cfg", return_value={"enabled": True}), \
                unittest.mock.patch.object(sys, "stderr", buf):
            code = pretool_gate.orch_check_edit("src/app/models/user.py")
        self.assertEqual(code, 2)
        return buf.getvalue()

    def test_message_names_every_tree_the_predicate_accepts(self):
        message = self.deny_message()
        self.assertTrue(pretool_gate.ORCH_ALLOW_PREFIXES, "allowlist must not be empty")
        for prefix in pretool_gate.ORCH_ALLOW_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertTrue(pretool_gate.orch_allowed_path(prefix + "note.md"),
                                f"predicate rejects its own allowlisted tree {prefix}")
                self.assertIn(prefix, message,
                              f"deny message does not name the allowlisted tree {prefix}")

    def test_predicate_accepts_no_tree_outside_the_shared_tuple(self):
        # A tree added as a branch inside orch_allowed_path() instead of to the
        # tuple would be accepted while the message stayed silent - the exact
        # FR-13 shape. Nothing enumerable proves its absence, so this pins the
        # source: the only directory-prefix literals the function may contain
        # are the ones in the shared tuple.
        source = inspect.getsource(pretool_gate.orch_allowed_path)
        literals = re.findall(r"""["']([^"'\n]*)["']""", source)
        stray = sorted({s.lower() for s in literals
                        if re.fullmatch(r"[A-Za-z0-9_.-]+/", s)
                        and s.lower() not in pretool_gate.ORCH_ALLOW_PREFIXES})
        self.assertEqual([], stray,
                         f"orch_allowed_path() hardcodes tree prefix(es) {stray} outside "
                         f"ORCH_ALLOW_PREFIXES, so the deny message cannot name them")


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


class ForbidDevNullGateTest(unittest.TestCase):
    """The Unix-redirect deny, driven rather than assumed (task-0067).

    The gate has existed since onboarding and `gates.forbid_dev_null` shipped as
    `false`, so the deny at check_destructive_and_repl() never ran: the rule was
    stated in CLAUDE.md, in rules/quality-standard.md and in the user's global
    instructions, and enforced nowhere. It rested on everyone remembering, and
    during this very task the orchestrator used the redirect and was allowed.

    Why it matters on this host: git-bash presents a Unix-looking shell over a
    Windows filesystem, so the redirect does not discard output - it creates a
    literal `nul` file in the working directory.

    The forbidden token is assembled from pieces on purpose. This file is read by
    humans grepping for the pattern, and a test that has to contain the thing it
    forbids should at least not look like an example to copy."""

    UNIX_NULL = "/" + "dev" + "/" + "null"

    def drive(self, command: str, flag: bool = True) -> tuple[int, str]:
        """handle_bash() with `forbid_dev_null` forced, and stderr captured. The
        real entry point, not the helper underneath it, so this covers the wiring
        as well as the pattern."""
        cfg = dict(pretool_gate.gates_cfg())
        cfg["forbid_dev_null"] = flag
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg), \
                unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(f"ls foo {command}", cwd=".")
        return code, err.getvalue()

    def test_the_shipped_config_has_the_gate_switched_on(self):
        # The flag itself, read from the project's real pipeline.json. Every
        # other assertion here forces the value, so without this one the suite
        # would stay green with the gate disabled again - which is exactly how
        # it went unnoticed.
        self.assertTrue(pretool_gate.gates_cfg().get("forbid_dev_null"),
                        "gates.forbid_dev_null is off in .agentry/pipeline.json")

    def test_every_unix_redirect_spelling_is_denied_with_the_reason(self):
        for form in (f">{self.UNIX_NULL} 2>&1", f"2>{self.UNIX_NULL}",
                     f"&>{self.UNIX_NULL}", f"> {self.UNIX_NULL}"):
            with self.subTest(redirect=form):
                code, msg = self.drive(form)
                self.assertEqual(2, code)
                # The message has to say WHY, or the next person just works
                # around it. It names the environment and the alternative.
                self.assertIn("literal `nul` file", msg)
                self.assertIn("NUL", msg)

    def test_a_command_without_the_redirect_is_allowed(self):
        # The control: without it every assertion above could pass because
        # handle_bash denies `ls` for some unrelated reason.
        for command in ("", "> out.txt 2>&1", "| head -5"):
            with self.subTest(command=command):
                self.assertEqual(0, self.drive(command)[0])

    def test_the_windows_native_forms_the_rule_permits_still_pass(self):
        # The rule says to use these instead, so the deny must not catch them.
        # It does not: the check is a substring test for the Unix path, and
        # `NUL` is a device name with no slash in it.
        for form in ("2>NUL", ">NUL", ">NUL 2>&1"):
            with self.subTest(redirect=form):
                self.assertEqual(0, self.drive(form)[0])

    def test_the_flag_is_what_denies_it_and_not_some_other_rule(self):
        # With the flag off, the identical command is allowed. This is what
        # makes the deny attributable to this gate rather than to the
        # destructive-pattern list or anything else in the chain.
        command = f">{self.UNIX_NULL} 2>&1"
        self.assertEqual(2, self.drive(command, flag=True)[0])
        self.assertEqual(0, self.drive(command, flag=False)[0])


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

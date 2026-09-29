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

import agent_gate
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

    What is true on this host, both spellings finally measured (task-0070 test
    stage, git-bash MINGW64, 2026-09-14):

      * `2>/dev/null` DISCARDS the output and creates nothing. MSYS2 provides a
        real character device (`test -c /dev/null` succeeds). The reason this
        gate carried for months - that it "creates a literal `nul` file" - was
        folklore, and this deny is policy, not physics.
      * `2>NUL` is the spelling that actually breaks. bash has no device named
        NUL, so it opens a file: `ls missing 2>NUL` in an empty directory leaves
        a 63-byte entry named NUL containing the error text. Windows then
        resolves that name as the reserved device, so Python cannot unlink it
        (WinError 5) and only a bash `rm -f` clears it.

    So the correct advice is still "do not redirect", but for one spelling out of
    two, and for a different reason than was written down.

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
                # around it. It names the environment and what to do instead.
                #
                # task-0070 test stage: it used to assert the message calls this
                # redirect the thing that "creates a literal `nul` file". That
                # claim was measured FALSE on this host - /dev/null is a real
                # character device under MINGW64, the output is discarded and
                # nothing is created. So the assertion now pins the corrected
                # reason (policy, not filesystem) plus a guard against the
                # folklore coming back. Same coupling, true claim.
                self.assertIn("denied by project policy", msg)
                self.assertNotIn("creates a literal `nul` file", msg)
                # task-0070: it used to name `>NUL` / `2>NUL` as the alternative,
                # which reproduces the defect - measured under git-bash, `ls
                # missing 2>NUL` in an empty directory creates a regular file
                # named NUL. The message must warn against that form rather than
                # recommend it, and must point at what actually works.
                self.assertIn("`>NUL` / `2>NUL` is NOT the fix", msg)
                self.assertIn("Drop the redirect", msg)
                self.assertIn("tmp/", msg)

    def test_a_command_without_the_redirect_is_allowed(self):
        # The control: without it every assertion above could pass because
        # handle_bash denies `ls` for some unrelated reason.
        for command in ("", "> out.txt 2>&1", "| head -5"):
            with self.subTest(command=command):
                self.assertEqual(0, self.drive(command)[0])

    def test_the_nul_forms_are_not_caught_by_this_particular_gate(self):
        # Behaviour pinned, claim corrected (task-0070). This gate is a
        # substring test for the Unix path, and `NUL` has no slash in it, so
        # these forms pass HERE. That is not an endorsement, and the measurement
        # makes it awkward: these are the forms that DO leave a file behind,
        # while the one this gate denies does not. Nothing enforces the NUL
        # forms yet - this test documents the gap, it does not bless them.
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


class QuotingAwareRedirectTest(unittest.TestCase):
    """task-0078: the gate denied text that merely DESCRIBES the redirect.

    Denial 1, measured 2026-09-14 while closing task-0070 and reproduced here
    against the unchanged code before the fix. The commit message documenting
    the redirect measurement was written through a heredoc, and the gate read
    the literal in the MESSAGE BODY as the act itself:

        Redirect to /dev/null is denied by project policy
        (gates.forbid_dev_null). The reason once given on this deny was wrong
        and has been removed: measured on this host 2026-09-14 under git-bash
        MINGW64, /dev/null IS a real character device here, ...

    The rule itself is untouched - `gates.forbid_dev_null` is the CEO's and only
    he relaxes it. What changed is that the gate now asks whether a REDIRECT
    OPERATOR sits outside quotes, instead of asking whether the literal appears
    anywhere in the command text.

    The forbidden token is assembled from pieces for the same reason
    ForbidDevNullGateTest does it: this file is read by humans grepping for the
    pattern and must not look like an example to copy."""

    UNIX_NULL = "/" + "dev" + "/" + "null"

    def test_every_real_redirect_spelling_is_still_detected(self):
        # Glued, spaced, appended, both descriptors, and the input direction.
        for form in (f"ls 2>{self.UNIX_NULL}", f"ls > {self.UNIX_NULL}",
                     f"ls &>{self.UNIX_NULL}", f"ls >> {self.UNIX_NULL}",
                     f"echo hi>{self.UNIX_NULL}", f"ls < {self.UNIX_NULL}",
                     f"ls > '{self.UNIX_NULL}'"):
            with self.subTest(form=form):
                self.assertTrue(pretool_gate.redirects_to_dev_null(form))

    def test_operator_and_target_as_two_separate_tokens_is_a_redirect(self):
        """The acceptance criterion a naive token-equality fix cannot satisfy:
        `2>` and the target are TWO tokens here, and together they are still a
        real redirect."""
        self.assertTrue(pretool_gate.redirects_to_dev_null(f"ls 2> {self.UNIX_NULL}"))

    def test_the_literal_inside_a_quoted_argument_is_not_a_redirect(self):
        for form in (f"python m.py --fix 'never write 2>{self.UNIX_NULL}'",
                     f'python m.py --fix "2>{self.UNIX_NULL}"'):
            with self.subTest(form=form):
                self.assertFalse(pretool_gate.redirects_to_dev_null(form))

    def test_the_literal_inside_a_heredoc_body_is_not_a_redirect(self):
        body = (f"git commit -F - <<'MSG'\ndocs: record that 2>{self.UNIX_NULL} "
                f"discards output\nMSG")
        self.assertFalse(pretool_gate.redirects_to_dev_null(body))

    def test_an_ordinary_file_redirect_is_not_the_forbidden_target(self):
        self.assertFalse(pretool_gate.redirects_to_dev_null("ls > tmp/build.log"))

    def test_an_arrow_is_not_a_redirect(self):
        self.assertFalse(pretool_gate.redirects_to_dev_null(f"echo 'a -> b' {self.UNIX_NULL}"))

    def test_the_deny_still_fires_end_to_end_through_handle_bash(self):
        cfg = dict(pretool_gate.gates_cfg())
        cfg["forbid_dev_null"] = True
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg), \
                unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(f"ls foo 2>{self.UNIX_NULL}", cwd=".")
        self.assertEqual(2, code)
        self.assertIn("forbid_dev_null", err.getvalue())

    def test_documenting_the_rule_in_a_heredoc_is_no_longer_the_deny(self):
        cfg = dict(pretool_gate.gates_cfg())
        cfg["forbid_dev_null"] = True
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg), \
                unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(
                f"cat > docs/redirects.md <<'EOF'\nNever use 2>{self.UNIX_NULL}.\nEOF", cwd=".")
        self.assertEqual(0, code)
        self.assertNotIn("forbid_dev_null", err.getvalue())


class HeredocBodyIsDataTest(unittest.TestCase):
    """task-0078 denial 4, measured 2026-09-16 and again 2026-09-29: the
    orchestrator writing a TASK FILE through a heredoc was denied because the
    markdown DESCRIBED a trunk push. Verbatim:

        This push cannot be resolved to a target branch, so the gate refuses
        rather than guessing (an unresolvable push gates). Run git push
        directly instead of wrapping it in another shell, and name the branch:
        git push -u origin <type>/task-<id>. The CEO merges via a PR.

    The command wrote markdown; no git ran."""

    TASK_FILE = ("cat > .agentry/tasks/backlog/task-0100.md <<'EOF'\n"
                 "## Notes\n\nSolo mode still needs a real git push origin main "
                 "to publish the trunk.\nEOF")

    def test_a_task_file_describing_a_push_is_not_a_push(self):
        self.assertFalse(pretool_gate.git_invokes(self.TASK_FILE, "push"))

    def test_the_backtick_spelling_is_not_a_push_either(self):
        cmd = ("cat > .agentry/tasks/backlog/task-0100.md <<'EOF'\n"
               "A `git push` of the trunk publishes it.\nEOF")
        self.assertFalse(pretool_gate.git_invokes(cmd, "push"))

    def test_the_write_itself_is_allowed_end_to_end(self):
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(self.TASK_FILE, cwd=".")
        self.assertEqual(0, code, err.getvalue())

    def test_a_heredoc_body_a_shell_could_execute_is_kept(self):
        """Fail-closed: `bash <<EOF` runs its body as a script, so nothing is
        stripped and the push inside it is still seen."""
        cmd = "bash <<'EOF'\ngit push origin main\nEOF"
        self.assertEqual(cmd, pretool_gate.strip_heredocs(cmd))
        self.assertTrue(pretool_gate.git_invokes(cmd, "push"))

    def test_a_herestring_is_not_a_heredoc(self):
        cmd = "grep x <<<'EOF is a word here'"
        self.assertEqual(cmd, pretool_gate.strip_heredocs(cmd))

    def test_separator_token_set_matches_the_padder(self):
        # SEPARATOR_TOKENS is spelled out above SHELL_SEPARATORS in the file;
        # the two must not drift apart.
        self.assertEqual(set(pretool_gate.SHELL_SEPARATORS),
                         set(pretool_gate.SEPARATOR_TOKENS))


class GitVerbInProseTest(unittest.TestCase):
    """task-0078 denial 2, measured 2026-09-14: recording the lesson about
    denial 1 into the memory store was itself denied, because the branch gate
    saw a git verb inside a `--fix` PROSE FIELD of a memory.py call and applied
    branch rules to a command that touches no repository at all:

        task-XXXX has no row in the run store (...), so the checkpoint approval
        cannot be verified and the gate refuses rather than assuming approval.

    The old second layer was `f"git {sub}" in command.lower()` - a substring net
    that cannot tell an invocation from a mention."""

    PROSE = ('python .claude/tools/memory/memory.py --record --kind lesson '
             '--fix "never let a git commit message quote the forbidden literal"')

    def test_a_git_verb_in_a_prose_argument_is_not_an_invocation(self):
        self.assertFalse(pretool_gate.git_invokes(self.PROSE, "commit", "push"))

    def test_the_memory_call_is_allowed_end_to_end(self):
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(self.PROSE, cwd=".")
        self.assertEqual(0, code, err.getvalue())

    def test_a_real_invocation_is_still_resolved(self):
        for form in ("git commit -m x", "git -C /repo commit -m x",
                     "cd /repo && git commit -m x", "ls && git commit -m x",
                     "git status; git commit -m x", "git status&&git commit -m x"):
            with self.subTest(form=form):
                self.assertTrue(pretool_gate.git_invokes(form, "commit"))

    def test_a_nested_shell_body_is_still_resolved(self):
        self.assertTrue(pretool_gate.git_invokes('bash -c "git push origin main"', "push"))

    def test_untokenisable_text_is_still_refused(self):
        """Fail-closed, deliberately: an unbalanced quote means the gate cannot
        tell which subcommand would run."""
        self.assertIsNone(pretool_gate.gate_tokens('git commit -m "don\'t'))
        self.assertTrue(pretool_gate.git_unparseable('git commit -m "don\'t'))
        self.assertTrue(pretool_gate.runs_approve_script('python x.py -m "don\'t'))


class ExecutedNamesTest(unittest.TestCase):
    """The position rule behind denial 3: a script NAME in an argument is not an
    invocation of that script."""

    def test_an_interpreter_runs_its_script_argument(self):
        self.assertEqual(["python", "a/approve.py"],
                         pretool_gate.executed_names("python a/approve.py --task t"))

    def test_flags_before_the_script_are_skipped(self):
        self.assertEqual(["python", "b/approve.py"],
                         pretool_gate.executed_names("python -u b/approve.py"))

    def test_leading_assignments_do_not_hide_the_interpreter(self):
        self.assertEqual(["python", "approve.py"],
                         pretool_gate.executed_names("FOO=1 python approve.py"))

    def test_a_path_argument_of_another_command_is_not_executed(self):
        self.assertEqual(["git"],
                         pretool_gate.executed_names("git show sha -- a/approve.py"))

    def test_each_simple_command_contributes_its_own_argv0(self):
        self.assertEqual(["ls", "python", "b/approve.py"],
                         pretool_gate.executed_names("ls && python b/approve.py"))

    def test_untokenisable_text_yields_none(self):
        self.assertIsNone(pretool_gate.executed_names("echo 'unbalanced"))

    def test_a_wrapper_argv0_does_not_hide_the_interpreter(self):
        """C2: `env`, `sudo`, `nohup`, `time`, `command`, `exec` and `xargs` all
        run their first non-flag argument, so argv0 is the word BEHIND them."""
        for command, expected in (
            ("env python a/approve.py", ["python", "a/approve.py"]),
            ("nohup python a/approve.py", ["python", "a/approve.py"]),
            ("command python a/approve.py", ["python", "a/approve.py"]),
            ("exec python a/approve.py", ["python", "a/approve.py"]),
            ("env FOO=1 python a/approve.py", ["python", "a/approve.py"]),
        ):
            with self.subTest(command=command):
                self.assertEqual(expected, pretool_gate.executed_names(command))

    def test_an_interpreter_reading_a_script_from_stdin_runs_it(self):
        for command in ("python < a/approve.py", "python <a/approve.py",
                        "python 0< a/approve.py"):
            with self.subTest(command=command):
                self.assertIn("a/approve.py", pretool_gate.executed_names(command))

    def test_a_pipe_into_an_interpreter_runs_the_piped_operand(self):
        for command in ("cat a/approve.py | python",
                        "echo a/approve.py | xargs python"):
            with self.subTest(command=command):
                self.assertIn("a/approve.py", pretool_gate.executed_names(command))

    def test_a_pipe_into_a_non_interpreter_runs_nothing_extra(self):
        self.assertEqual(["cat", "grep"],
                         pretool_gate.executed_names("cat a/approve.py | grep x"))


class HeredocQuoteAwarenessTest(unittest.TestCase):
    """task-0078 re-implementation, C1: strip_heredocs() scanned the raw text
    with no notion of quoting and swallowed an unterminated body to the end of
    the text, so a quoted MENTION of a heredoc opener deleted every command
    after it.

        echo "see <<EOF in docs"
        git push origin main

    stripped to `echo "see  in docs"` and the push became invisible to every
    gate. Heredoc stripping must never delete text that would otherwise be
    scanned: the opener is recognised only outside quotes, and a missing
    terminator keeps the remaining lines."""

    UNIX_NULL = "/" + "dev" + "/" + "null"
    HIDDEN_PUSH = 'echo "see <<EOF in docs"\ngit push origin main'

    def test_a_quoted_heredoc_opener_does_not_swallow_the_next_command(self):
        self.assertIn("git push origin main",
                      pretool_gate.strip_heredocs(self.HIDDEN_PUSH))
        self.assertTrue(pretool_gate.git_invokes(self.HIDDEN_PUSH, "push"))

    def test_the_hidden_push_is_denied_to_a_readonly_agent(self):
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = agent_gate.handle_readonly("Bash", {"command": self.HIDDEN_PUSH})
        self.assertEqual(2, code)
        self.assertIn("mutating Bash denied", err.getvalue())

    def test_a_quoted_opener_does_not_hide_a_real_redirect(self):
        command = f'echo "documented <<EOF here"\nls 2>{self.UNIX_NULL}'
        self.assertTrue(pretool_gate.redirects_to_dev_null(command))

    def test_an_unterminated_heredoc_keeps_the_remaining_lines(self):
        command = "cat > f <<EOF\ngit push origin main\nno terminator here"
        self.assertIn("git push origin main", pretool_gate.strip_heredocs(command))
        self.assertTrue(pretool_gate.git_invokes(command, "push"))

    def test_a_command_on_the_next_line_is_its_own_command(self):
        """A newline terminates a command, but shlex eats it as whitespace, so
        the second line arrived as an ARGUMENT of the first - `echo "x"` then a
        file mutation read as one `echo` call with extra words."""
        self.assertEqual(["echo", "hi"],
                         pretool_gate.command_segments("echo hi\nls -la")[0])
        self.assertTrue(agent_gate.bash_mutates('echo "doc <<EOF here"\nchmod 777 f'))

    def test_a_terminated_heredoc_body_is_still_data(self):
        command = ("cat > .agentry/tasks/backlog/task-0100.md <<'EOF'\n"
                   "Solo mode still needs a real git push origin main.\n"
                   "EOF\necho done")
        stripped = pretool_gate.strip_heredocs(command)
        self.assertNotIn("git push", stripped)
        self.assertIn("echo done", stripped)
        self.assertFalse(pretool_gate.git_invokes(command, "push"))


class NestedRedirectTest(unittest.TestCase):
    """task-0078 re-implementation, C3: redirects_to_dev_null() never recursed
    into a nested shell body, unlike redirect_write_target() and git_invokes()
    next to it, so `bash -c 'ls >/dev/null'` was allowed where the bare form was
    denied. `eval` carries the same body in a different wrapper."""

    UNIX_NULL = "/" + "dev" + "/" + "null"

    def deny_code(self, command: str) -> tuple:
        cfg = dict(pretool_gate.gates_cfg())
        cfg["forbid_dev_null"] = True
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg), \
                unittest.mock.patch("sys.stderr", err):
            code = pretool_gate.handle_bash(command, cwd=".")
        return code, err.getvalue()

    def test_a_nested_shell_body_redirect_is_detected(self):
        for command in (f"bash -c 'ls >{self.UNIX_NULL}'",
                        f'sh -c "ls 2>{self.UNIX_NULL}"',
                        f"bash -lc 'ls >> {self.UNIX_NULL}'"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.redirects_to_dev_null(command))

    def test_a_nested_shell_body_redirect_is_denied_end_to_end(self):
        code, msg = self.deny_code(f"bash -c 'ls >{self.UNIX_NULL}'")
        self.assertEqual(2, code)
        self.assertIn("forbid_dev_null", msg)

    def test_a_nested_body_that_only_mentions_the_target_is_allowed(self):
        command = f"bash -c 'echo \"never write 2>{self.UNIX_NULL}\"'"
        self.assertFalse(pretool_gate.redirects_to_dev_null(command))
        code, msg = self.deny_code(command)
        self.assertEqual(0, code, msg)

    def test_eval_with_a_redirect_operator_is_refused(self):
        for command in (f"eval 'ls >{self.UNIX_NULL}'", "eval 'ls > $X'",
                        'eval "ls 2> $TARGET"'):
            with self.subTest(command=command):
                code, msg = self.deny_code(command)
                self.assertEqual(2, code)
                self.assertIn("eval", msg)

    def test_eval_without_a_redirect_operator_is_allowed(self):
        code, msg = self.deny_code("eval 'ls -la'")
        self.assertEqual(0, code, msg)


class CommandSubstitutionTest(unittest.TestCase):
    """task-0078 re-implementation, H1: `$( ... )` and backticks run a command
    the shell splits differently from shlex - `echo $(git push origin main)`
    tokenises to `$(git`, `push`, `main)`, so the git regex never matched a
    `git` token and the push was invisible. Quoted, `"$(git push)"` is one
    token and equally invisible. The inner text is now analysed as a command in
    its own right."""

    UNIX_NULL = "/" + "dev" + "/" + "null"

    def test_a_substituted_git_call_is_resolved(self):
        for command in ("echo $(git push origin main)",
                        'echo "$(git push origin main)"',
                        "echo `git push origin main`",
                        "X=$(git commit -m x)"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.git_invokes(command, "push", "commit"))

    def test_a_substituted_push_is_denied_to_a_readonly_agent(self):
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = agent_gate.handle_readonly(
                "Bash", {"command": "echo $(git push origin main)"})
        self.assertEqual(2, code)
        self.assertIn("mutating Bash denied", err.getvalue())

    def test_a_substituted_redirect_is_detected(self):
        self.assertTrue(pretool_gate.redirects_to_dev_null(
            f"echo $(ls 2>{self.UNIX_NULL})"))

    def test_a_substitution_inside_single_quotes_is_not_expanded(self):
        # Single quotes suppress substitution, so this really is prose.
        self.assertFalse(pretool_gate.git_invokes("echo '$(git push origin main)'", "push"))

    def test_arithmetic_expansion_is_not_a_redirect(self):
        self.assertEqual("", pretool_gate.redirect_write_target('echo "$(( 2 > 1 ))"'))

    def test_an_unbalanced_substitution_fails_closed(self):
        self.assertTrue(pretool_gate.git_invokes("echo $(git push origin main", "push"))

    def test_read_only_substitutions_stay_allowed(self):
        for command in ("echo $(git rev-parse HEAD)", "echo `git status --short`"):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.bash_mutates(command))


class MaskLineAlignmentTest(unittest.TestCase):
    """task-0078 second review, C1: mask_quoted() replaced the newlines INSIDE a
    quoted span with filler, so `mask_quoted(cmd).split("\\n")` produced fewer
    lines than `cmd.split("\\n")` and split_heredocs() indexed `masked[i]` off
    the end. The IndexError propagated to main(), which catches Exception and
    allows - so a multi-line quoted argument did not merely evade one check, it
    switched the whole gate off. Measured before the fix: all three of these
    exited 0."""

    UNIX_NULL = "/" + "dev" + "/" + "null"
    HIDDEN_PUSH = "git commit -m 'msg\n<<' && git push origin main"
    HIDDEN_RM = "echo 'note\n<<' && rm -rf src"

    def test_the_mask_keeps_the_line_count_of_the_original(self):
        for command in (self.HIDDEN_PUSH, self.HIDDEN_RM, "x='a\nb\nc'\nls"):
            with self.subTest(command=command):
                self.assertEqual(len(command.split("\n")),
                                 len(pretool_gate.mask_quoted(command).split("\n")))

    def test_split_heredocs_does_not_raise_on_a_multiline_quoted_argument(self):
        pretool_gate.split_heredocs(self.HIDDEN_PUSH)  # used to raise IndexError

    def test_the_push_behind_a_multiline_quote_is_resolved(self):
        self.assertTrue(pretool_gate.git_invokes(self.HIDDEN_PUSH, "push"))

    def test_the_push_behind_a_multiline_quote_is_denied(self):
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = agent_gate.handle_readonly("Bash", {"command": self.HIDDEN_PUSH})
        self.assertEqual(2, code)
        self.assertIn("mutating Bash denied", err.getvalue())

    def test_the_rm_behind_a_multiline_quote_is_denied_to_a_readonly_agent(self):
        self.assertIn("rm", agent_gate.bash_mutates(self.HIDDEN_RM))
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            code = agent_gate.handle_readonly("Bash", {"command": self.HIDDEN_RM})
        self.assertEqual(2, code)

    def test_the_discard_redirect_behind_a_multiline_quote_is_detected(self):
        command = f"echo 'note\n<<' && ls 2>{self.UNIX_NULL}"
        self.assertTrue(pretool_gate.redirects_to_dev_null(command))


class ParserTotalityFuzzTest(unittest.TestCase):
    """task-0078 second review, C1 guard. A gate that raises is an ALLOW here
    (every main() catches Exception by design, so a bug cannot brick the agent),
    which makes totality over arbitrary text a security property rather than a
    style preference. Every parser in the file is run over a small cross product
    of quote / newline / heredoc-operator shapes: none may raise, and where a
    real mutation follows the noise it must still be denied."""

    NOISE = ("'a\n<<'", '"a\n<<"', "'<<EOF'", '"<<"', "'\n'", '"x\ny"',
             "<<", "<<EOF", "'unclosed", '"unclosed\n<<', "`a\nb`",
             "$(a\n<<)", "''", '""', "'<<\n<<\n<<'")
    PARSERS = ("mask_quoted", "strip_heredocs", "split_heredocs",
               "command_substitutions", "substitution_bodies",
               "nested_command_bodies", "executed_names", "shell_c_bodies",
               "eval_operands", "redirect_write_target", "redirects_to_dev_null",
               "runs_approve_script")

    def test_no_parser_raises_on_any_quote_newline_heredoc_combination(self):
        for noise in self.NOISE:
            for tail in ("", " && git push origin main", "\nrm -rf src", " > out.txt"):
                command = f"echo {noise}{tail}"
                for name in self.PARSERS:
                    with self.subTest(command=command, parser=name):
                        try:
                            getattr(pretool_gate, name)(command)
                        except Exception as exc:  # catching everything IS the test
                            self.fail(f"{name} raised {exc!r} on {command!r}")
                with self.subTest(command=command, parser="bash_mutates"):
                    try:
                        agent_gate.bash_mutates(command)
                    except Exception as exc:  # catching everything IS the test
                        self.fail(f"bash_mutates raised {exc!r} on {command!r}")

    def test_a_real_mutation_after_the_noise_is_still_denied(self):
        for noise in self.NOISE:
            for tail in (" && git push origin main", "\nrm -rf src"):
                command = f"echo {noise}{tail}"
                with self.subTest(command=command):
                    self.assertNotEqual("", agent_gate.bash_mutates(command))


class SubstitutionDepthTest(unittest.TestCase):
    """task-0078 second review, H1: exceeding MAX_SHELL_DEPTH was an ALLOW, and
    every `$(` cost a level, so `echo $($($($(git push origin main))))` - two
    free characters per level - ran past the ceiling and was allowed against a
    protected branch (measured, exit 0). A substitution is not a nested shell:
    the bodies are flattened onto the one depth charge the outermost pays, and
    running out of depth is now a deny."""

    def test_a_four_deep_substituted_push_is_resolved(self):
        self.assertTrue(pretool_gate.git_invokes(
            "echo $($($($(git push origin main))))", "push"))

    def test_an_eight_deep_substituted_push_is_resolved(self):
        command = "echo " + "$(" * 8 + "git push origin main" + ")" * 8
        self.assertTrue(pretool_gate.git_invokes(command, "push"))

    def test_a_deeply_substituted_push_is_denied_to_a_readonly_agent(self):
        for depth in (4, 8):
            command = "echo " + "$(" * depth + "git push origin main" + ")" * depth
            with self.subTest(depth=depth):
                err = io.StringIO()
                with unittest.mock.patch("sys.stderr", err):
                    code = agent_gate.handle_readonly("Bash", {"command": command})
                self.assertEqual(2, code)

    def test_a_two_deep_real_shell_is_still_resolved(self):
        command = 'bash -c \'bash -c "git push origin main"\''
        self.assertTrue(pretool_gate.git_invokes(command, "push"))

    def test_running_out_of_depth_is_a_deny_not_an_allow(self):
        command = 'bash -c "bash -c \'git status\'"'
        self.assertTrue(pretool_gate.unscannable_depth(
            command, pretool_gate.MAX_SHELL_DEPTH))
        self.assertFalse(pretool_gate.unscannable_depth(
            "git status", pretool_gate.MAX_SHELL_DEPTH))

    def test_a_read_only_substitution_stack_stays_allowed(self):
        self.assertEqual("", agent_gate.bash_mutates("echo $($(git rev-parse HEAD))"))


class ModuleInterpreterFlagTest(unittest.TestCase):
    """task-0078 second review, M1: exec_names_of() read the script an
    interpreter is handed as an argument or on stdin, but never its `-m`
    operand, so `python3 -m approve` named no script and passed every profile."""

    COMMAND = ("cd .claude/tools/pipeline && "
               "python3 -m approve --task task-0078 --gate commit")

    def test_the_module_resolves_to_its_script_path(self):
        self.assertIn("approve.py", pretool_gate.executed_names(self.COMMAND))
        self.assertIn("pipeline/approve.py", pretool_gate.executed_names(
            "python -m pipeline.approve --gate commit"))
        self.assertTrue(pretool_gate.runs_approve_script(self.COMMAND))

    def test_the_module_invocation_is_denied_to_every_profile(self):
        for handler in (agent_gate.handle_dev, agent_gate.handle_docs,
                        agent_gate.handle_readonly):
            with self.subTest(handler=handler.__name__):
                err = io.StringIO()
                with unittest.mock.patch("sys.stderr", err):
                    code = handler("Bash", {"command": self.COMMAND})
                self.assertEqual(2, code)
                self.assertIn("approve.py", err.getvalue())

    def test_a_module_that_is_not_a_gated_script_stays_allowed(self):
        self.assertFalse(pretool_gate.runs_approve_script(
            "python -m unittest discover -s .claude/tools"))


if __name__ == "__main__":
    unittest.main()

"""Integration tests for pipeline/pretool_gate.py against real git repositories.

The pure-function tests in test_pretool_gate.py mock git away entirely. That
leaves task-0001's actual acceptance criteria unverified: they are phrased as
"in a temporary repository with zero commits, a git commit command ... is
allowed" - a claim about handle_bash() driven by real git plumbing, not about
matches_c2() or planning_only_commit() in isolation. This file drives
handle_bash() against throwaway git repos created with the real `git` binary.

Standard library only: subprocess + tempfile create and manipulate the repos.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import agent_gate
import pretool_gate


def run_git(args: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=10, check=True)


class TempRepo:
    """A throwaway git repo, deleted on exit. The initial branch is always
    named 'main' regardless of the host's git config, so tests are
    deterministic across machines."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name
        run_git(["init", "-q", "-b", "main"], self.path)
        run_git(["config", "user.email", "qa@example.com"], self.path)
        run_git(["config", "user.name", "QA"], self.path)
        return self

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()

    def write(self, *names: str) -> None:
        for name in names:
            p = Path(self.path, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")

    def stage(self, *names: str) -> None:
        self.write(*names)
        run_git(["add", *names], self.path)

    def commit(self, name: str = "seed.txt", message: str = "seed") -> None:
        self.stage(name)
        run_git(["commit", "-q", "-m", message], self.path)

    def checkout_new(self, branch: str, start_point: str | None = None) -> None:
        args = ["checkout", "-q", "-b", branch]
        if start_point:
            args.append(start_point)
        run_git(args, self.path)


class BootstrapExemptionTest(unittest.TestCase):
    """FR-1: a repo with zero commits, or whose configured main does not
    exist, allows a git commit without requiring a task branch."""

    def test_zero_commit_repo_allows_commit(self):
        with TempRepo() as repo:
            repo.stage("README.md")
            code = pretool_gate.handle_bash("git commit -m init", cwd=repo.path)
        self.assertEqual(code, 0)

    def test_zero_commit_repo_allows_commit_of_non_planning_source_file(self):
        # This is the scenario task-0001 was written for: the very first
        # commit of a repo carries real source code, not just docs, so the
        # FR-21 C-2 exemption alone would refuse it. Only the FR-1 bootstrap
        # exemption - checked before any branch/path logic - can allow it.
        with TempRepo() as repo:
            repo.stage(".claude/tools/pipeline/state.py")
            code = pretool_gate.handle_bash("git commit -m init", cwd=repo.path)
        self.assertEqual(code, 0)

    def test_head_exists_but_configured_main_missing_still_allows_commit(self):
        with TempRepo() as repo:
            repo.commit()
            run_git(["branch", "-m", "main", "trunk"], repo.path)  # main no longer exists
            code = pretool_gate.handle_bash("git commit --allow-empty -m more", cwd=repo.path)
        self.assertEqual(code, 0)


class BranchGateUnchangedTest(unittest.TestCase):
    """FR-1 second half: once main exists, the pre-existing branch-based
    denial for commits is unchanged - the bootstrap exemption does not leak
    past the first commit."""

    def test_main_exists_non_task_branch_is_denied(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("some-branch")
            repo.stage("app.py")
            code = pretool_gate.handle_bash("git commit -m work", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_main_exists_proper_task_branch_is_allowed(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("bugfix/task-9981")
            code = pretool_gate.handle_bash("git commit -m work", cwd=repo.path)
        self.assertEqual(code, 0)


class FR21ExemptionTest(unittest.TestCase):
    """FR-21: a commit whose diff touches only the C-2 planning/doc set is
    allowed without a task branch."""

    def test_planning_only_commit_on_main_is_allowed(self):
        with TempRepo() as repo:
            repo.commit()
            repo.stage(".claude/plans/x.md", "docs/y.md")
            code = pretool_gate.handle_bash("git commit -m plan", cwd=repo.path)
        self.assertEqual(code, 0)

    def test_same_files_on_proper_task_branch_also_allowed_unchanged(self):
        # On a real task branch, the C-2 exemption is never even consulted -
        # task_from_branch() resolves a task id and the pre-existing
        # "not under pipeline control" fallback applies. This proves FR-21
        # did not change that path.
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("bugfix/task-9982")
            repo.stage(".claude/plans/x.md", "docs/y.md")
            code = pretool_gate.handle_bash("git commit -m plan", cwd=repo.path)
        self.assertEqual(code, 0)


class FR22MixedDiffTest(unittest.TestCase):
    """FR-22: the exemption is all-or-nothing. One path outside the C-2 set
    disqualifies the whole commit - checked against every pair named in
    task-0001's acceptance criteria, not just one generic example."""

    SECOND_PATHS = (
        ".claude/tools/pipeline/state.py",
        ".gitignore",
        ".claude/settings.json",
        ".agentry/pipeline.json",
    )

    def test_each_named_pair_is_refused(self):
        for second_path in self.SECOND_PATHS:
            with self.subTest(second_path=second_path):
                with TempRepo() as repo:
                    repo.commit()
                    repo.stage(".agentry/plans/x.md", second_path)
                    code = pretool_gate.handle_bash("git commit -m mix", cwd=repo.path)
                self.assertEqual(code, 2)

    def test_denial_message_names_the_branch_gate(self):
        # Exercises the real hook entry point (main() reading JSON from
        # stdin), not just the internal handle_bash() function. The payload's
        # "cwd" field is the repo pretool_gate.py resolves against - it is
        # independent of the subprocess's own OS-level working directory, so
        # it must be supplied explicitly here to target the temp repo instead
        # of falling back to state.ROOT (the real project checkout).
        import json
        with TempRepo() as repo:
            repo.commit()
            repo.stage(".agentry/plans/x.md", ".gitignore")
            payload = json.dumps({"tool_name": "Bash",
                                   "tool_input": {"command": "git commit -m mix"},
                                   "cwd": repo.path})
            proc = subprocess.run(
                [sys.executable, str(PIPELINE_DIR / "pretool_gate.py")],
                cwd=repo.path, input=payload,
                capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("task-XXXX", proc.stderr)

    def test_all_paths_inside_c2_is_still_allowed(self):
        # Control case alongside the mixed-pair matrix above: same shape, both
        # paths legitimately inside C-2.
        with TempRepo() as repo:
            repo.commit()
            repo.stage(".agentry/plans/x.md", "docs/y.md")
            code = pretool_gate.handle_bash("git commit -m plan", cwd=repo.path)
        self.assertEqual(code, 0)


class BranchCreationStillEnforcedTest(unittest.TestCase):
    """AC #2: branch-naming and branch-base denials are unchanged outside the
    bootstrap case. These predate task-0001 but had zero test coverage."""

    def test_invalid_branch_name_denied_even_after_bootstrap(self):
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash("git checkout -b randomname", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_valid_branch_cut_from_main_allowed(self):
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash(
                "git checkout -b bugfix/task-9983 main", cwd=repo.path)
        self.assertEqual(code, 0)

    def test_valid_branch_cut_from_non_main_denied(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("side-branch")
            code = pretool_gate.handle_bash(
                "git checkout -b bugfix/task-9984 side-branch", cwd=repo.path)
        self.assertEqual(code, 2)


class RenameExemptionHoleTest(unittest.TestCase):
    """FR-22 must not be defeated by git's default rename detection: a `git mv`
    of real code into a C-2 path must still show its pre-image (real code)
    path in the staged diff, disqualifying the exemption."""

    def test_staged_rename_of_real_code_into_c2_path_is_refused(self):
        with TempRepo() as repo:
            repo.commit(".claude/tools/pipeline/state.py")
            Path(repo.path, ".claude/plans").mkdir(parents=True, exist_ok=True)
            run_git(["mv", ".claude/tools/pipeline/state.py", ".claude/plans/x.md"],
                    repo.path)
            code = pretool_gate.handle_bash("git commit -m sneaky", cwd=repo.path)
        self.assertEqual(code, 2)


class DashACommitHoleTest(unittest.TestCase):
    """FR-22 must not be defeated by `git commit -a`/`-am`/`--all`, which
    stages every dirty tracked file after the index was inspected."""

    def test_commit_dash_a_with_unrelated_dirty_file_is_refused(self):
        with TempRepo() as repo:
            repo.commit("app.py")
            Path(repo.path, "app.py").write_text("dirty change", encoding="utf-8")
            repo.stage(".claude/plans/x.md")
            code = pretool_gate.handle_bash("git commit -am sneaky", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_commit_all_long_flag_with_unrelated_dirty_file_is_refused(self):
        with TempRepo() as repo:
            repo.commit("app.py")
            Path(repo.path, "app.py").write_text("dirty change", encoding="utf-8")
            repo.stage(".claude/plans/x.md")
            code = pretool_gate.handle_bash("git commit --all -m sneaky", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_plain_commit_without_dash_a_still_exempt(self):
        # Control: same planning-only staging, no -a, remains allowed.
        with TempRepo() as repo:
            repo.commit("app.py")
            repo.stage(".claude/plans/x.md")
            code = pretool_gate.handle_bash("git commit -m plan", cwd=repo.path)
        self.assertEqual(code, 0)


class BootstrapNonRepoFirstCandidateTest(unittest.TestCase):
    """FR-1: when the first candidate path (e.g. `git -C <dir>`) is not a git
    repository at all, repo_bootstrap must fall through to the next candidate
    instead of mistaking "not a repo" for "unborn HEAD".

    The earlier version of this class only asserted the allow case, which passed
    against the buggy repo_bootstrap too - and in fact never reached it at all,
    because `git -C <dir> commit` did not register as a commit before the argv
    resolution fix. The denial case below is the one that actually pins the
    behavior: a buggy repo_bootstrap returns True for the non-repo candidate and
    grants a bootstrap exemption, turning this exit 2 into exit 0."""

    def test_first_candidate_not_a_repo_falls_through_to_bootstrap_repo(self):
        with tempfile.TemporaryDirectory() as not_a_repo:
            with TempRepo() as repo:
                repo.stage("README.md")
                command = f'git -C "{not_a_repo}" commit -m init'
                self.assertTrue(pretool_gate.repo_bootstrap(command, repo.path, "main"))
                code = pretool_gate.handle_bash(command, cwd=repo.path)
            self.assertEqual(code, 0)

    def test_non_repo_candidate_does_not_grant_exemption_to_a_mature_repo(self):
        with tempfile.TemporaryDirectory() as not_a_repo:
            with TempRepo() as repo:
                repo.commit()
                repo.checkout_new("some-branch")
                repo.stage("app.py")
                command = f'git -C "{not_a_repo}" commit -m work'
                self.assertFalse(pretool_gate.repo_bootstrap(command, repo.path, "main"))
                code = pretool_gate.handle_bash(command, cwd=repo.path)
            self.assertEqual(code, 2)


class GitGlobalOptionsTest(unittest.TestCase):
    """A global option between `git` and its subcommand (`git -C dir push`,
    `git -c k=v commit`, `git --no-pager commit`) must not hide the subcommand
    from the gate. Substring matching missed all three, which skipped the whole
    commit/push block - branch gate, approval flags and protected-branch deny
    included."""

    def test_dash_c_push_to_protected_branch_is_denied(self):
        # No " origin main" in the text, so the deny can only come from resolving
        # the subcommand and reading the branch - not from a substring match.
        with TempRepo() as repo:
            repo.commit()  # HEAD stays on main
            command = f'git -C "{repo.path}" push'
            code = pretool_gate.handle_bash(command, cwd=repo.path)
        self.assertEqual(code, 2)

    def test_dash_c_push_to_protected_branch_is_denied_from_unrelated_cwd(self):
        # The repo is reachable only through `git -C`, so this also proves the
        # branch is read from the repo the command targets, not from cwd.
        with tempfile.TemporaryDirectory() as elsewhere:
            with TempRepo() as repo:
                repo.commit()
                command = f'git -C "{repo.path}" push origin main'
                code = pretool_gate.handle_bash(command, cwd=elsewhere)
        self.assertEqual(code, 2)

    def test_dash_lowercase_c_config_commit_hits_the_branch_gate(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("some-branch")
            repo.stage("app.py")
            code = pretool_gate.handle_bash(
                "git -c user.name=x commit -m work", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_no_pager_commit_hits_the_branch_gate(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("some-branch")
            repo.stage("app.py")
            code = pretool_gate.handle_bash("git --no-pager commit -m work", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_dash_c_branch_creation_is_name_checked(self):
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash(
                f'git -C "{repo.path}" checkout -b randomname', cwd=repo.path)
        self.assertEqual(code, 2)

    def test_dash_c_valid_branch_from_main_still_allowed(self):
        # Control: the fix must not turn every global-option git call into a deny.
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash(
                f'git -C "{repo.path}" checkout -b bugfix/task-9985 main', cwd=repo.path)
        self.assertEqual(code, 0)

    def test_read_only_git_with_global_options_is_untouched(self):
        with TempRepo() as repo:
            repo.commit()
            for command in ("git --no-pager log --oneline",
                            "git --version",
                            f'git -C "{repo.path}" status --short',
                            "git merge-base --is-ancestor main HEAD"):
                with self.subTest(command=command):
                    self.assertEqual(pretool_gate.handle_bash(command, cwd=repo.path), 0)


class GitArgvResolutionTest(unittest.TestCase):
    """Unit-level coverage of the shared helper every gate now routes through."""

    def test_global_options_are_skipped(self):
        cases = {
            "git push origin main": "push",
            'git -C /tmp/x push origin main': "push",
            "git -c user.name=x commit -m y": "commit",
            "git --no-pager commit -m y": "commit",
            "git --git-dir=/tmp/x/.git commit -m y": "commit",
            "git --git-dir /tmp/x/.git commit -m y": "commit",
            "git --exec-path=/usr/lib/git-core push": "push",
            "/usr/bin/git -C /tmp/x push": "push",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(
                    [sub for sub, _ in pretool_gate.git_invocations(command)], [expected])

    def test_option_argument_is_not_mistaken_for_the_subcommand(self):
        # `-C push` consumes "push" as the directory - the real subcommand is status.
        self.assertEqual(
            [sub for sub, _ in pretool_gate.git_invocations("git -C push status")], ["status"])

    def test_chain_yields_one_entry_per_link(self):
        subs = [sub for sub, _ in pretool_gate.git_invocations(
            "git add . && git -c k=v commit -m x ; git push origin main")]
        self.assertEqual(subs, ["add", "commit", "push"])

    def test_unparseable_command_is_gated_not_allowed(self):
        command = 'git -C "unbalanced commit -m x'
        self.assertEqual(pretool_gate.git_invocations(command),
                         [(pretool_gate.GIT_UNKNOWN, [])])
        self.assertTrue(pretool_gate.git_invokes(command, "push"))

    def test_git_without_subcommand_is_resolved_not_unknown(self):
        self.assertEqual(pretool_gate.git_invocations("git --version"), [])
        self.assertFalse(pretool_gate.git_invokes("git --version", "commit", "push"))

    def test_non_git_command_is_ignored(self):
        self.assertEqual(pretool_gate.git_invocations("digit commit -m x"), [])
        self.assertFalse(pretool_gate.git_invokes("ls -la", "commit"))

    def test_nested_shell_form_still_matches_via_substring_net(self):
        self.assertTrue(pretool_gate.git_invokes('bash -c "git push origin main"', "push"))


class AgentGateGitMutationTest(unittest.TestCase):
    """The subagent gate shared the same substring bug, so a readonly or docs
    agent could mutate the tree with one global option. It now routes through
    the same argv resolution."""

    def test_global_option_does_not_hide_a_mutating_subcommand(self):
        for command in ('git -C /tmp/x push origin main',
                        'git -c user.name=x commit -m y',
                        'git --no-pager reset --hard',
                        'git -C /tmp/x stash pop'):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.bash_mutates(command))

    def test_read_only_git_stays_allowed(self):
        for command in ("git merge-base --is-ancestor main HEAD",
                        "git --no-pager log --oneline",
                        'git -C /tmp/x status --short',
                        "git stash list"):
            with self.subTest(command=command):
                self.assertEqual(agent_gate.bash_mutates(command), "")

    def test_force_push_behind_a_global_option_is_detected(self):
        self.assertTrue(agent_gate.is_force_push('git -C /tmp/x push --force origin main'))
        self.assertTrue(agent_gate.is_force_push("git push --force-with-lease"))
        self.assertFalse(agent_gate.is_force_push("git push origin main"))


class GluedSeparatorTest(unittest.TestCase):
    """A shell separator with no surrounding whitespace made shlex glue two
    commands into one token ('status&&git'), so argv resolution never saw the
    second `git` and every readonly/docs agent could wipe the tree. These seven
    commands are the ones verified as exit 0 before the fix."""

    GLUED = (
        "git status&&git add -A",
        "git status&&git reset --hard HEAD",
        "git status&&git clean -fd",
        "git diff&&git checkout -- .",
        "true&&git rebase -i HEAD~3",
        "git status&&git stash drop",
        "cd .;;git reset --hard HEAD",
    )

    def test_argv_resolution_sees_the_second_git_call(self):
        for command in self.GLUED:
            with self.subTest(command=command):
                self.assertTrue(agent_gate.git_mutates(command),
                                "glued separator hid the mutating git call")

    def test_denied_under_readonly_and_docs_profiles(self):
        for profile, handler in (("readonly", agent_gate.handle_readonly),
                                  ("docs", agent_gate.handle_docs)):
            for command in self.GLUED:
                with self.subTest(profile=profile, command=command):
                    self.assertEqual(handler("Bash", {"command": command}), 2)

    def test_other_glued_forms_are_caught_too(self):
        for command in ("git status;git add -A",
                        "git status|git add -A",
                        "git status||git push origin x",
                        "ls&git commit -m x"):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.git_mutates(command))

    def test_read_only_chains_stay_allowed(self):
        for command in ("git status&&git log --oneline",
                        "git diff&&git merge-base --is-ancestor main HEAD",
                        "git status;git stash list"):
            with self.subTest(command=command):
                self.assertEqual(agent_gate.bash_mutates(command), "")

    def test_quoted_separators_are_not_rewritten(self):
        # Padding must stay outside quotes: a commit message keeps its own text.
        subs = pretool_gate.git_invocations('git commit -m "fix a && b"')
        self.assertEqual(subs, [("commit", ["-m", "fix a && b"])])


class GitMutationFallbackTest(unittest.TestCase):
    """Second layer: a regex net behind argv resolution, mirroring the net
    git_invokes() already carries, for git text argv walking cannot see."""

    def test_nested_shell_form_is_caught_by_the_regex_net(self):
        for command in ('bash -c "git add -A"',
                        "sh -c 'git reset --hard HEAD'",
                        'bash -c "git commit -m x"'):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.git_mutates(command))

    def test_fallback_does_not_fire_on_read_only_git(self):
        for command in ('bash -c "git merge-base --is-ancestor main HEAD"',
                        'bash -c "git log --merges --oneline"',
                        'bash -c "git log --author=add"',
                        'bash -c "git status --short"'):
            with self.subTest(command=command):
                self.assertEqual(agent_gate.git_mutates(command), "")

    def test_nested_shell_form_is_denied_under_readonly_and_docs(self):
        for profile, handler in (("readonly", agent_gate.handle_readonly),
                                  ("docs", agent_gate.handle_docs)):
            with self.subTest(profile=profile):
                self.assertEqual(
                    handler("Bash", {"command": 'bash -c "git add -A"'}), 2)


class FallbackFalsePositiveTest(unittest.TestCase):
    """M1: run over the whole string, the regex net matched a listed word that
    was merely an ARGUMENT of a read-only git command, so reading a diff was
    denied to the very agents that do nothing else. The net now runs only when
    argv resolution found no invocation at all."""

    READ_ONLY = (
        "git diff HEAD -- add.py",       # matched 'git add'
        "git log --grep add",            # matched 'git add'
        "git log --oneline -- reset.py",
        "git show HEAD:merge.md",
        "git diff --stat -- clean.js",
    )

    def test_argument_named_like_a_mutating_sub_is_not_a_mutation(self):
        for command in self.READ_ONLY:
            with self.subTest(command=command):
                self.assertEqual(agent_gate.git_mutates(command), "")

    def test_allowed_under_readonly_and_docs_profiles(self):
        for profile, handler in (("readonly", agent_gate.handle_readonly),
                                  ("docs", agent_gate.handle_docs)):
            for command in self.READ_ONLY:
                with self.subTest(profile=profile, command=command):
                    self.assertEqual(handler("Bash", {"command": command}), 0)


class GluedSeparatorBranchGateTest(unittest.TestCase):
    """H: the same glued separator returned ('', []) from new_branch_name(),
    which skipped BOTH the branch-naming gate and the branch-base gate."""

    def test_glued_branch_creation_name_is_resolved(self):
        self.assertEqual(
            pretool_gate.new_branch_name("git status&&git checkout -b badname"),
            ("badname", []))

    def test_glued_invalid_branch_creation_is_denied(self):
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash(
                "git status&&git checkout -b badname", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_glued_valid_branch_from_non_main_is_denied(self):
        with TempRepo() as repo:
            repo.commit()
            repo.checkout_new("side-branch")
            code = pretool_gate.handle_bash(
                "git status&&git checkout -b bugfix/task-9986 side-branch", cwd=repo.path)
        self.assertEqual(code, 2)

    def test_glued_valid_branch_from_main_still_allowed(self):
        with TempRepo() as repo:
            repo.commit()
            code = pretool_gate.handle_bash(
                "git status&&git checkout -b bugfix/task-9987 main", cwd=repo.path)
        self.assertEqual(code, 0)


class UnparseableGitTest(unittest.TestCase):
    """GIT_UNKNOWN must gate at EVERY call site, and say why. new_branch_name()
    was the one caller reading the sentinel as 'not a git command', and the
    fall-through deny blamed the commit approval for a quoting error."""

    def test_new_branch_name_returns_the_sentinel(self):
        self.assertEqual(pretool_gate.new_branch_name('git checkout -b evil "broken'),
                         (pretool_gate.GIT_UNKNOWN, []))

    def test_unparseable_branch_creation_is_denied(self):
        with TempRepo() as repo:
            repo.commit()
            self.assertEqual(
                pretool_gate.check_branch_creation('git checkout -b evil "broken',
                                                    repo.path), 2)
            self.assertEqual(
                pretool_gate.handle_bash('git checkout -b evil "broken', cwd=repo.path), 2)

    def test_parse_failure_message_names_the_real_cause(self):
        # A heredoc carrying an apostrophe was refused with "Commit for task-XXXX
        # is not approved yet" - the wrong cause entirely.
        import contextlib
        import io
        command = "cat <<'EOF' > note.txt\ngit history: don't guess\nEOF"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(command)
        self.assertEqual(code, 2)
        self.assertIn("cannot be tokenised", err.getvalue())
        self.assertNotIn("not approved yet", err.getvalue())

    def test_unparseable_git_is_still_mutating_for_agents(self):
        self.assertTrue(agent_gate.git_mutates('git checkout -b evil "broken'))


class AgentGateWebToolTest(unittest.TestCase):
    """bypassPermissions removed the permission prompt, and the gate's matcher
    did not cover web tools - research agents may read the web, but it must be
    auditable."""

    def test_research_profiles_are_allowed_and_logged(self):
        for profile in ("docs", "readonly"):
            with self.subTest(profile=profile):
                calls = []
                original = agent_gate.log_web_access
                agent_gate.log_web_access = lambda *a: calls.append(a)
                try:
                    code = agent_gate.handle_web(profile, "WebFetch",
                                                 {"url": "https://example.com"})
                finally:
                    agent_gate.log_web_access = original
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 1)

    def test_dev_profile_is_denied(self):
        self.assertEqual(agent_gate.handle_web("dev", "WebSearch", {"query": "x"}), 2)


if __name__ == "__main__":
    unittest.main()

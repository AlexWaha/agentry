"""task-0004: the pieces ported from the `fin.local` tree per the FR-4 inventory.

One test per ported mechanism, because each one exists to close a measured
incident and a port without a test is a claim that the port worked:

  FR-6  stack_gate.py      a stage's gate resolved from the task's `repo:`, and
                           a no-op rather than a red gate for a repo with no
                           application stack.
  FR-7  lanes              PIPELINE_LANE gives a session its own run.db, mode
                           and approvals file; unset is the old behaviour.
  FR-5  check_glob()       the artifact gate against any path pattern.
  FR-5  ui_evidence.safe() a non-ASCII UI label in the evidence must not fail
                           the stage on a cp1252 console.
        is_narrowed()      a dev agent may run a NARROWED suite, never the full
                           one - two tests shipped red under the strict split.
        repos_for_task()   a colliding task id in an unrelated repo is not this
                           task's unmerged branch.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import agent_gate
import artifact_gate
import git_state
import pretool_gate
import stack_gate
import state
import ui_evidence


def _force_writable(func, target, _exc):
    """rmtree onerror: git marks its object files read-only, which makes the
    delete fail on Windows and leaves the next run a stale directory."""
    os.chmod(target, stat.S_IWRITE)
    func(target)


class StackGateTest(unittest.TestCase):
    """FR-6. The template ships the mechanism with an EMPTY stack table, so the
    honest default for every repo is a no-op gate rather than another stack's
    suite. task-0029 (FR-44) moves the table into pipeline.json."""

    def test_template_ships_no_project_literals(self):
        self.assertEqual({}, stack_gate.STACKS)
        self.assertEqual({}, stack_gate.STACK_CWD)

    def test_an_unconfigured_repo_has_no_commands(self):
        self.assertEqual([], stack_gate.commands("infra", "test"))

    def test_a_configured_repo_dispatches_its_own_commands(self):
        with unittest.mock.patch.object(
                stack_gate, "STACKS",
                {"backend": {"test": ["run the backend suite"]}}):
            self.assertEqual(["run the backend suite"],
                             stack_gate.commands("backend", "test"))

    def test_a_subdirectory_value_resolves_to_its_repo(self):
        # Task files carry both `backend` and `backend/src` for one repo.
        with unittest.mock.patch.object(
                stack_gate, "STACKS", {"backend": {"test": ["x"]}}):
            self.assertEqual("backend", stack_gate.resolve_stack("backend/src"))
            self.assertEqual("backend", stack_gate.resolve_stack("/backend/"))

    def test_a_stage_the_stack_omits_is_a_no_op_not_an_error(self):
        with unittest.mock.patch.object(
                stack_gate, "STACKS", {"backend": {"test": ["x"]}}):
            self.assertEqual([], stack_gate.commands("backend", "review"))

    def _main(self, task="task-0100", stage="test"):
        argv = ["stack_gate.py", "--task", task, "--stage", stage]
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out):
            return stack_gate.main(), out.getvalue()

    def test_an_empty_table_exits_zero_and_runs_nothing(self):
        # The property that matters: with no stack configured the gate is a
        # no-op that PASSES, and it says so rather than implying it checked.
        with unittest.mock.patch.object(state, "task_repo", return_value="infra"), \
                unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self._main()
        self.assertEqual(0, code)
        never_run.assert_not_called()
        self.assertIn("NOT CONFIGURED", printed)

    def test_a_configured_repo_runs_its_commands_and_returns_their_code(self):
        cwd = stack_gate.ROOT / "backend"
        with unittest.mock.patch.object(
                    stack_gate, "STACKS", {"backend": {"test": ["the suite"]}}), \
                unittest.mock.patch.object(stack_gate, "STACK_CWD", {"backend": cwd}), \
                unittest.mock.patch.object(state, "task_repo", return_value="backend"), \
                unittest.mock.patch.object(stack_gate, "run", return_value=3) as ran:
            code, printed = self._main()
        self.assertEqual(3, code)
        ran.assert_called_once_with(["the suite"], cwd)
        self.assertEqual("", printed)

    def test_a_missing_cwd_entry_falls_back_to_the_workspace_root(self):
        # A half-filled table degrades to a working gate rather than raising.
        with unittest.mock.patch.object(
                    stack_gate, "STACKS", {"backend": {"test": ["the suite"]}}), \
                unittest.mock.patch.object(stack_gate, "STACK_CWD", {}), \
                unittest.mock.patch.object(state, "task_repo", return_value="backend"), \
                unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran:
            code, _ = self._main()
        self.assertEqual(0, code)
        ran.assert_called_once_with(["the suite"], stack_gate.ROOT)


class TaskRepoTest(unittest.TestCase):
    """state.task_repo() is the single reader of the `repo:` field, shared by
    stack_gate.py and git_state.repos_for_task()."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="task_repo_"))
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            d = self.tmp / name
            d.mkdir()
            self.dirs[name] = d
        p = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for d in self.dirs.values():
            for f in d.glob("*.md"):
                f.unlink()
            d.rmdir()
        self.tmp.rmdir()

    def _write(self, task, where="active", repo_line="repo: backend", body=""):
        (self.dirs[where] / f"{task}.md").write_text(
            f"---\nid: {task.split('-')[1]}\n{repo_line}\n---\n\n{body}",
            encoding="utf-8")

    def test_reads_the_field_from_any_folder(self):
        self._write("task-0100", where="done")
        self.assertEqual("backend", state.task_repo("task-0100"))

    def test_quotes_are_stripped(self):
        self._write("task-0101", repo_line="repo: 'frontend'")
        self.assertEqual("frontend", state.task_repo("task-0101"))

    def test_a_blank_field_is_none(self):
        self._write("task-0102", repo_line="repo:")
        self.assertIsNone(state.task_repo("task-0102"))

    def test_a_missing_file_is_none(self):
        self.assertIsNone(state.task_repo("task-0999"))

    def test_the_word_in_the_body_is_not_the_field(self):
        # Only the head of the file is read, so prose cannot pose as frontmatter.
        self._write("task-0103", repo_line="repo:",
                    body="\n" * 60 + "repo: not-the-field\n")
        self.assertIsNone(state.task_repo("task-0103"))


class ReposForTaskTest(unittest.TestCase):
    """Task ids are allocated per workspace and consumed per repo, so they
    collide. An unscoped scan read an unrelated repo's branch as this task's
    unmerged work and parked it forever."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repo_scope_"))
        self.backend = self.root / "backend"
        self.frontend = self.root / "frontend"
        for d in (self.backend, self.frontend):
            d.mkdir()
        p = unittest.mock.patch.object(state, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        p = unittest.mock.patch.object(git_state, "repos",
                                       return_value=[self.backend, self.frontend])
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for d in (self.backend, self.frontend):
            d.rmdir()
        self.root.rmdir()

    def _declares(self, value):
        return unittest.mock.patch.object(state, "task_repo", return_value=value)

    def test_a_declared_repo_scopes_the_scan_to_it(self):
        with self._declares("backend"):
            self.assertEqual([self.backend], git_state.repos_for_task("task-1236"))

    def test_a_subdirectory_value_still_resolves_to_the_repo(self):
        with self._declares("backend/src"):
            self.assertEqual([self.backend], git_state.repos_for_task("task-1236"))

    def test_no_declared_repo_means_every_repo(self):
        with self._declares(None):
            self.assertEqual([self.backend, self.frontend],
                             git_state.repos_for_task("task-1236"))

    def test_a_value_matching_nothing_fails_open_to_every_repo(self):
        # Fail-open: a stale or misspelled value must not make a task unclosable.
        with self._declares("no-such-repo"):
            self.assertEqual([self.backend, self.frontend],
                             git_state.repos_for_task("task-1236"))


class NestedShellRedirectTest(unittest.TestCase):
    """mask_quoted() blanks a quoted span, and a quoted span can be a whole
    nested command: `bash -c "echo x > src/app.py"` was scanned as the empty
    string, so the redirect gate saw nothing and both the readonly profile and
    orchestrator_gate allowed a file write. The body is now unwrapped and
    scanned recursively.

    Diffed in BOTH directions per the comment in pretool_gate.py: the refusals
    below are new, and the allowances below must stay allowances."""

    WRAPPED = (
        'bash -c "echo pwned > src/app.py"',
        "sh -c 'echo pwned > src/app.py'",
        'bash -c "echo pwned >> src/app.py"',
        'zsh -c "cat x > src/app.py"',
        'bash -lc "echo pwned > src/app.py"',
        'bash -c "bash -c \'echo pwned > src/app.py\'"',
    )
    STILL_ALLOWED = (
        'python -c "print(2 > 1)"',                 # quoted operator, not a redirect
        "git log --oneline -5",
        'bash -c "pytest -k order"',                # nested, but read-only
        "bash -c 'cat src/app.py'",
        'bash -c "ls 2>&1"',                        # descriptor dup
        'bash -c "ls > NUL"',                       # discard sink
        'grep -n "a > b" src/app.py',
    )

    def test_a_wrapped_redirect_is_seen(self):
        for command in self.WRAPPED:
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.redirect_write_target(command),
                                "nested redirect went undetected")

    def test_a_direct_redirect_is_still_seen(self):
        self.assertEqual("> src/app.py",
                         pretool_gate.redirect_write_target("echo pwned > src/app.py"))

    def test_read_only_commands_are_still_allowed(self):
        for command in self.STILL_ALLOWED:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_the_readonly_profile_denies_both_spellings(self):
        for command in ("echo pwned > src/app.py", *self.WRAPPED):
            with self.subTest(command=command):
                self.assertEqual(2, agent_gate.handle_readonly(
                    "Bash", {"command": command}))

    def test_a_script_path_is_not_read_as_a_c_body(self):
        # `bash deploy.sh -c` has no -c BODY; the positional argument ends the scan.
        self.assertEqual([], pretool_gate.shell_c_bodies("bash deploy.sh -c"))
        self.assertEqual(["echo hi"], pretool_gate.shell_c_bodies('bash -c "echo hi"'))

    def test_recursion_is_bounded(self):
        # A self-referential body must not recurse forever.
        deep = 'bash -c "' * 8 + "echo x" + '"' * 8
        self.assertEqual("", pretool_gate.redirect_write_target(deep))


class LaneValidationTest(unittest.TestCase):
    """FR-7, hardened. A lane name becomes part of a filename, so an unvalidated
    value silently turned the checkpoints off: `a/b` raised a sqlite error the
    hooks' fail-open swallowed into exit 0, and `../../evil` created a database
    outside the state directory. An unusable name now warns and falls back to
    the default lane, and the checkpoint is held one layer down: a lane MISMATCH
    (valid name, wrong lane) is an ordinary event and must refuse the commit,
    not allow it."""

    GATE = Path(pretool_gate.__file__)
    # Assembled, so this file's own text carries no literal git write command
    # for the gate to match when the suite is edited.
    COMMIT = "git " + "com" + "mit -m x"
    # 9xxx, like every other throwaway id in this suite: never registered in a
    # real run store, so the run-row refusal below is reachable by construction.
    TASK = "task-9049"
    BRANCH = f"bugfix/{TASK}"

    @contextlib.contextmanager
    def _task_branch_repo(self):
        """A throwaway repo with a commit on main and HEAD on a task branch.

        task-0049: the commit refusals below are only reachable once the gate
        resolves a task FROM THE BRANCH NAME, so a test that read the ambient
        HEAD passed on a task branch and failed on the trunk - where the
        branch-name check answers first with a different reason and the same
        exit 2. A test states its own preconditions. Same shape as TempRepo in
        test_pretool_gate_git.py and LocalMergeDetectionTest in
        test_conveyor_gaps.py, including the read-only cleanup: git leaves its
        object files read-only, which makes a plain rmtree fail on Windows.
        """
        tmp = Path(tempfile.mkdtemp(prefix="lane_branch_"))
        repo = tmp / "work"
        repo.mkdir()

        def git(*args):
            p = subprocess.run(["git", "-C", str(repo), *args],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(0, p.returncode, f"git {' '.join(args)}: {p.stderr}")

        try:
            git("init", "-q", "-b", "main")
            git("config", "user.email", "qa@example.com")
            git("config", "user.name", "QA")
            (repo / "seed.txt").write_text("x", encoding="utf-8")
            git("add", "seed.txt")
            git("commit", "-q", "-m", "seed")
            git("checkout", "-q", "-b", self.BRANCH)
            self.assertEqual(
                self.BRANCH, pretool_gate.current_branch(cwd=str(repo)),
                "the gate must read OUR branch, not the ambient one")
            yield repo
        finally:
            shutil.rmtree(tmp, onerror=_force_writable)

    def _run_gate(self, lane, command, cwd):
        # task-0049: `cwd` is required, not defaulted to state.ROOT. A default
        # reintroduces the ambient-repo dependency this task removed the moment
        # a caller's command reaches the branch logic.
        env = dict(os.environ)
        env.pop("PIPELINE_LANE", None)
        if lane is not None:
            env["PIPELINE_LANE"] = lane
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(cwd), "agent_type": "test"}
        proc = subprocess.run(
            [sys.executable, str(self.GATE)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True, env=env, cwd=str(state.ROOT), timeout=60)
        return proc.returncode, (proc.stderr or b"").decode("utf-8", "replace")

    def test_valid_lane_names_pass_the_pattern(self):
        for name in ("planning", "night", "lane_2", "a-b", "A9", "x" * 32):
            with self.subTest(name=name):
                self.assertTrue(state.LANE_RE.match(name))

    def test_a_path_like_or_oversized_lane_is_rejected(self):
        for name in ("a/b", "../../evil", "a b", "x" * 33, ".", "a;b"):
            with self.subTest(name=name):
                self.assertIsNone(state.LANE_RE.match(name))

    def test_an_invalid_lane_warns_and_falls_back_to_the_default_lane(self):
        # It must NOT raise at import: state.py is imported at the top of every
        # hook, SystemExit escapes their `except Exception`, and a PreToolUse
        # exit 2 denies the tool call - so a typo denied Read and ls too, and a
        # Stop exit 2 forbade stopping. The lane still must not open a store of
        # its own anywhere (NFR-4, and stop_gate's "never trap the user").
        for name in ("a/b", "../../evil"):
            with self.subTest(name=name):
                # The real workspace on purpose: the assertions below are about
                # the real state directory holding no stray store.
                _, err = self._run_gate(name, "ls -la", cwd=str(state.ROOT))
                self.assertIn("PIPELINE_LANE", err)
                self.assertFalse(
                    list(state.STATE_DIR.glob("*evil*")),
                    "an invalid lane must not create a store outside its name")
                self.assertFalse(
                    list(Path(state.ROOT).glob("**/run.a.db")),
                    "an invalid lane must not create a store of its own")

    def test_an_invalid_lane_does_not_brick_the_session(self):
        # The three entry points the reviewer measured: a read-only tool call, a
        # stop decision, and the session bootstrap. None may exit 2.
        payload = {"tool_name": "Read", "tool_input": {"file_path": "README.md"},
                   "agent_type": "test"}
        code, err = self._run_hook(pretool_gate.__file__, "a/b", payload)
        self.assertEqual(0, code, "a read-only tool call must still be allowed")
        self.assertIn("PIPELINE_LANE", err)

        import stop_gate
        code, err = self._run_hook(stop_gate.__file__, "a/b",
                                   {"session_id": "x", "stop_hook_active": False})
        self.assertNotEqual(2, code, "exit 2 on Stop forbids stopping - no way out")
        self.assertIn("PIPELINE_LANE", err)

        bootstrap = Path(state.ROOT) / ".claude" / "tools" / "hooks" / "session_start.py"
        code, err = self._run_hook(bootstrap, "a/b", None)
        self.assertEqual(0, code)
        self.assertIn("PIPELINE_LANE", err)

    def _run_hook(self, script, lane, payload):
        env = dict(os.environ)
        env.pop("PIPELINE_LANE", None)
        env["PIPELINE_LANE"] = lane
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=b"" if payload is None else json.dumps(payload).encode("utf-8"),
            capture_output=True, env=env, cwd=str(state.ROOT), timeout=120)
        return proc.returncode, (proc.stderr or b"").decode("utf-8", "replace")

    def test_a_lane_typo_refuses_the_commit_instead_of_allowing_it(self):
        # A valid-but-wrong lane: its store has no row for this task, so the
        # approval cannot be verified. Fails closed, like check_merge_source.
        with self._task_branch_repo() as repo:
            code, err = self._run_gate("nosuchlane", self.COMMIT, cwd=repo)
        # Registered BEFORE the assertions: the gate really does create the
        # lane's store, and a trailing unlink leaves it behind on any failure,
        # polluting every later run.
        stray = state.STATE_DIR / "run.nosuchlane.db"
        self.addCleanup(lambda: stray.unlink(missing_ok=True))
        self.assertEqual(2, code)
        # Reached through the RUN-ROW check, not the branch-name check: the
        # message names the task the gate resolved from our branch, and it
        # names the LANE's store rather than the default one.
        self.assertIn("no row in the run store", err)
        self.assertIn(self.TASK, err)
        self.assertIn("run.nosuchlane.db", err)
        self.assertIn("PIPELINE_LANE", err)

    def test_the_default_lane_is_unchanged(self):
        # Same repo, same commit, no lane: the gate reaches the same run-row
        # refusal and names the UNSUFFIXED store. The previous version asserted
        # only that one substring was absent, which held on every branch by
        # construction - a check with no failing state proves nothing (recorded
        # lesson: proof-that-cannot-fail).
        with self._task_branch_repo() as repo:
            code, err = self._run_gate(None, self.COMMIT, cwd=repo)
        self.assertEqual(2, code)
        self.assertIn("no row in the run store", err)
        self.assertIn("run.db)", err)
        self.assertNotIn("run.nosuchlane.db", err)
        self.assertIn("PIPELINE_LANE is unset", err)


class LaneTest(unittest.TestCase):
    """FR-7. One conveyor per lane: PIPELINE_LANE suffixes the run store, the
    mode file and the approvals file. The lane is read in state.py and nowhere
    else, so the three cannot disagree about which lane the session is in."""

    def _reload(self, lane):
        env = dict(os.environ)
        env.pop("PIPELINE_LANE", None)
        if lane is not None:
            env["PIPELINE_LANE"] = lane
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            st = importlib.reload(state)
            return st, importlib.reload(importlib.import_module("mode")), \
                importlib.reload(importlib.import_module("approvals"))

    def tearDown(self):
        # Leave the modules as the rest of the suite expects them: default lane.
        self._reload(None)

    def test_unset_lane_is_the_old_paths_exactly(self):
        st, md, ap = self._reload(None)
        self.assertEqual("", st.LANE)
        self.assertEqual("", st.LANE_SUFFIX)
        self.assertEqual("run.db", st.DB_PATH.name)
        self.assertEqual("mode", md.MODE_PATH.name)
        self.assertEqual("approvals", ap.APPROVALS_PATH.name)

    def test_a_lane_suffixes_all_three_files(self):
        st, md, ap = self._reload("planning")
        self.assertEqual("planning", st.LANE)
        self.assertEqual(".planning", st.LANE_SUFFIX)
        self.assertEqual("run.planning.db", st.DB_PATH.name)
        self.assertEqual("mode.planning", md.MODE_PATH.name)
        self.assertEqual("approvals.planning", ap.APPROVALS_PATH.name)

    def test_mode_and_approvals_read_the_lane_from_state(self):
        # Not from the environment a second time - one accessor, one answer.
        st, md, ap = self._reload("night")
        self.assertTrue(md.MODE_PATH.name.endswith(st.LANE_SUFFIX))
        self.assertTrue(ap.APPROVALS_PATH.name.endswith(st.LANE_SUFFIX))

    def test_lanes_are_in_the_same_state_directory(self):
        st, md, ap = self._reload("planning")
        self.assertEqual(st.STATE_DIR, st.DB_PATH.parent)
        self.assertEqual(st.STATE_DIR, md.MODE_PATH.parent)
        self.assertEqual(st.STATE_DIR, ap.APPROVALS_PATH.parent)


class ModeSetFromConfigTest(unittest.TestCase):
    """The set of modes is a property of the project: everything except `talk`
    comes from pipeline.json, so a workspace declaring a `research` flow gets a
    `research` mode without editing this file."""

    def setUp(self):
        import mode
        self.mode = mode
        # A real file rather than a patched Path method: a Path instance's
        # read_text is read-only and cannot be mocked.
        self.tmp = Path(tempfile.mkdtemp(prefix="mode_file_"))
        p = unittest.mock.patch.object(mode, "MODE_PATH", self.tmp / "mode")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.glob("*"):
            f.unlink()
        self.tmp.rmdir()

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


class ArtifactGlobTest(unittest.TestCase):
    """FR-5 (artifact_gate row): the same two checks - names the task, carries
    real content - against any path pattern, for a project whose planning
    artifact is neither a plan nor a spec."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="artifact_glob_"))
        (self.root / "docs").mkdir()
        p = unittest.mock.patch.object(state, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in (self.root / "docs").glob("*"):
            f.unlink()
        (self.root / "docs").rmdir()
        self.root.rmdir()

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


class UiEvidenceEncodingTest(unittest.TestCase):
    """FR-5 (ui_evidence row): the gate message quotes the evidence file, so it
    carries whatever labels the UI uses. A non-ASCII label used to raise
    UnicodeEncodeError from print() on a cp1252 console and fail the stage for a
    reason unrelated to the evidence."""

    def test_a_non_ascii_label_is_replaced_not_carried(self):
        # chr() escapes rather than literals, so this file stays plain ASCII.
        label = chr(0x041a) + chr(0x043d) + chr(0x043e) + chr(0x043f)  # Cyrillic
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "cp1252"
            out = ui_evidence.safe(f"UI verified on the {label} screen")
        self.assertIn("UI verified on the", out)
        self.assertIn("screen", out)
        # The point of the function: what cp1252 cannot carry is GONE, and a
        # replacement marker is there instead. Asserting a length here is what
        # made the previous version of this test pass with safe() replaced by
        # the identity function - '????' is as long as the label it replaced.
        self.assertNotIn(label, out)
        for ch in label:
            self.assertNotIn(ch, out)
        self.assertIn("?", out)

    def test_a_real_cp1252_stream_takes_the_message_print_cannot(self):
        # The defect this function exists for, against a live stream rather than
        # a mock: the raw message raises, the sanitised one writes.
        label = chr(0x041a) + chr(0x043d)
        message = f"UI verified on the {label} screen"

        raw = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with unittest.mock.patch.object(sys, "stdout", raw), \
                self.assertRaises(UnicodeEncodeError):
            print(message)
            raw.flush()

        clean = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with unittest.mock.patch.object(sys, "stdout", clean):
            print(ui_evidence.safe(message))
            clean.flush()
        written = clean.buffer.getvalue()
        self.assertIn(b"UI verified on the", written)
        self.assertIn(b"?", written)
        self.assertNotIn(label.encode("utf-8"), written)

    def test_an_ascii_message_is_unchanged(self):
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "cp1252"
            self.assertEqual("2 routes, none failing",
                             ui_evidence.safe("2 routes, none failing"))

    def test_a_utf8_console_keeps_the_label(self):
        label = chr(0x041a) + chr(0x043d)
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "utf-8"
            self.assertIn(label, ui_evidence.safe(f"screen {label}"))

    def test_no_encoding_at_all_does_not_raise(self):
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = None
            self.assertEqual("plain", ui_evidence.safe("plain"))


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

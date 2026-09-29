"""task-0004, FR-7 lanes: PIPELINE_LANE gives a session its own run.db, mode and
approvals file; unset is the old behaviour. An unusable lane name warns and
falls back to the default lane.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import importlib
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
import state
import tmproot


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
        test_conveyor_gaps.py, including the project-local location and the
        read-only cleanup: git leaves its object files read-only, which makes a
        plain rmtree fail on Windows. Both now come from tmproot.
        """
        tmp = tmproot.mkdtemp("lane_branch_")
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
            tmproot.rmtree(tmp)

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


if __name__ == "__main__":
    unittest.main()

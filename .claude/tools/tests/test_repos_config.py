"""task-0028, FR-42: the `repos{}` object of pipeline.json (contract C-3) and the
git_state reader for it.

Every test builds its own workspace and config in a temp directory, so none of
them depends on the real pipeline.json carrying `repos`.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import git_state
import state
import tmproot


class ReposConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "repos_cfg_").resolve()
        (self.root / ".agentry").mkdir()
        for name, value in (("ROOT", self.root),
                            ("PIPELINE_PATH", self.root / ".agentry" / "pipeline.json")):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def config(self, repos=...):
        data = {} if repos is ... else {"repos": repos}
        (self.root / ".agentry" / "pipeline.json").write_text(
            json.dumps(data), encoding="utf-8")

    def entries(self):
        """repo_entries() and whatever it wrote to stderr."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return git_state.repo_entries(), err.getvalue()

    def test_an_object_config_is_read_with_its_stack_and_stages(self):
        self.config({"agentry": {"path": ".", "stack": "python",
                                 "stages": {"review": ["lint it"], "test": []}}})
        got, err = self.entries()
        self.assertEqual({"agentry": {"path": ".", "stack": "python",
                                      "stages": {"review": ["lint it"], "test": []}}}, got)
        self.assertEqual("", err)

    def test_repos_uses_each_entry_path_not_the_key(self):
        self.config({"api": {"path": "services/backend"}, "web": {"path": "web"}})
        self.assertEqual([self.root / "services" / "backend", self.root / "web"],
                         git_state.repos())

    def test_the_object_form_matches_auto_detect_for_a_root_repo(self):
        (self.root / ".git").mkdir()
        self.config()
        detected = git_state.repos()
        self.config({"agentry": {"path": ".", "stack": "python"}})
        self.assertEqual([self.root], detected)
        self.assertEqual(detected, git_state.repos())

    def test_the_object_form_matches_auto_detect_for_a_nested_repo(self):
        # The deployed root copy: the workspace root is not a repo, `src` is.
        (self.root / "src" / ".git").mkdir(parents=True)
        self.config()
        detected = git_state.repos()
        self.config({"agentry": {"path": "src", "stack": "python"}})
        self.assertEqual([self.root / "src"], detected)
        self.assertEqual(detected, git_state.repos())

    def test_the_legacy_list_form_is_still_accepted(self):
        self.config(["backend", "frontend"])
        got, err = self.entries()
        self.assertEqual({}, got)
        self.assertEqual("", err)
        self.assertEqual([self.root / "backend", self.root / "frontend"], git_state.repos())

    def test_an_absent_key_is_silent_and_falls_back_to_auto_detect(self):
        (self.root / "app" / ".git").mkdir(parents=True)
        self.config()
        got, err = self.entries()
        self.assertEqual({}, got)
        self.assertEqual("", err)
        self.assertEqual([self.root / "app"], git_state.repos())

    def test_a_missing_or_unparseable_pipeline_file_gives_no_entries(self):
        got, _ = self.entries()
        self.assertEqual({}, got)
        (self.root / ".agentry" / "pipeline.json").write_text("{not json", encoding="utf-8")
        got, _ = self.entries()
        self.assertEqual({}, got)

    def test_comment_keys_are_skipped_silently(self):
        self.config({"_comment": "what this is", "agentry": {"path": "."}})
        got, err = self.entries()
        self.assertEqual(["agentry"], list(got))
        self.assertEqual("", err)

    def test_a_bad_entry_is_dropped_and_reported_and_the_rest_survive(self):
        bad = {
            "not_object": "src",
            "no_path": {"stack": "python"},
            "empty_path": {"path": "  "},
            "rooted": {"path": "/etc"},
            "escaping": {"path": "../elsewhere"},
            "stack_type": {"path": "x", "stack": 3},
            "stages_type": {"path": "x", "stages": ["test"]},
            "cmds_type": {"path": "x", "stages": {"test": "pytest"}},
            "cmd_item_type": {"path": "x", "stages": {"test": ["ok", 7]}},
        }
        self.config({**bad, "good": {"path": "good"}})
        got, err = self.entries()
        self.assertEqual(["good"], list(got))
        for key in bad:
            self.assertIn(f"'{key}'", err)

    def test_a_drive_qualified_path_is_rejected(self):
        self.config({"x": {"path": "C:\\elsewhere"}, "y": {"path": "C:elsewhere"}})
        got, err = self.entries()
        # On a POSIX host these are ordinary file names; the rule is about
        # Windows drives, so only assert where the drive is recognised.
        if Path("C:\\x").anchor:
            self.assertEqual({}, got)
            self.assertIn("'x'", err)
            self.assertIn("'y'", err)

    def test_a_repos_value_of_the_wrong_type_is_reported_not_raised(self):
        self.config("backend")
        got, err = self.entries()
        self.assertEqual({}, got)
        self.assertIn("must be an object", err)

    def test_when_every_entry_is_bad_repos_falls_back_to_auto_detect(self):
        (self.root / "app" / ".git").mkdir(parents=True)
        self.config({"broken": {"stack": "python"}})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual([self.root / "app"], git_state.repos())

    def test_repo_path_resolves_a_key_and_none_for_an_unknown_one(self):
        self.config({"agentry": {"path": "src"}})
        self.assertEqual(self.root / "src", git_state.repo_path("agentry"))
        self.assertIsNone(git_state.repo_path("nope"))

    def test_repos_for_task_reads_the_object_form(self):
        # ReposForTaskTest in test_task_repo.py patches repos() and cannot see
        # the config reader; this goes through the real one.
        self.config({"a": {"path": "a"}, "b": {"path": "b"}})
        with unittest.mock.patch.object(state, "task_repo", return_value=None):
            self.assertEqual([self.root / "a", self.root / "b"],
                             git_state.repos_for_task("task-0001"))
        with unittest.mock.patch.object(state, "task_repo", return_value="b"):
            self.assertEqual([self.root / "b"], git_state.repos_for_task("task-0001"))


if __name__ == "__main__":
    unittest.main()

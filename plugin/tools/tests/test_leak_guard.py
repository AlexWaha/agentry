"""task-0041 L1: the leak guard in throwaway git repos.

A bare-less origin is not needed: `origin/main` is just a ref, so each case
creates it with `update-ref` after the seed commit.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import leak_guard
import tmproot

REPO_ROOT = Path(__file__).resolve().parents[3]


def git(args: list, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


class LeakGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = tmproot.sandbox(self, "leak_")
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        self.write(".agentry/project/stack.md", "template")
        self.write("README.md", "x")
        self.commit("seed")
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)

    def write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> None:
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", message], self.repo)

    def test_clean_repo_exits_zero(self):
        self.assertEqual(leak_guard.check("HEAD", self.repo), (0, []))

    def test_tracked_template_edit_is_not_a_leak(self):
        self.write(".agentry/project/stack.md", "edited")
        self.write("src.txt", "code")
        self.commit("edit")
        self.assertEqual(leak_guard.check("HEAD", self.repo), (0, []))

    def test_planted_files_each_exit_one(self):
        for rel in (".agentry/memory/lessons.md", ".agentry/project/secret.md",
                    ".agentry/tasks/active/task-0001.md"):
            with self.subTest(rel=rel):
                self.write(rel, "internal")
                self.commit("plant")
                self.assertEqual(leak_guard.check("HEAD", self.repo), (1, [rel]))
                git(["reset", "-q", "--hard", "origin/main"], self.repo)

    def test_rename_into_agentry_exits_one(self):
        git(["mv", "README.md", ".agentry/moved.md"], self.repo)
        self.commit("rename")
        self.assertEqual(leak_guard.check("HEAD", self.repo), (1, [".agentry/moved.md"]))

    def test_missing_origin_main_exits_two(self):
        git(["update-ref", "-d", "refs/remotes/origin/main"], self.repo)
        code, lines = leak_guard.check("HEAD", self.repo)
        self.assertEqual(code, 2)
        self.assertTrue(lines)

    def test_unknown_ref_exits_two(self):
        self.assertEqual(leak_guard.check("no-such-ref", self.repo)[0], 2)

    def test_not_a_repo_exits_two(self):
        bare = tmproot.sandbox(self, "leak_norepo_")
        self.assertEqual(leak_guard.check("HEAD", bare)[0], 2)


    def test_add_then_remove_still_exits_one(self):
        self.write(".agentry/memory/secret.db", "internal")
        self.commit("plant")
        git(["rm", "-q", ".agentry/memory/secret.db"], self.repo)
        self.commit("remove")
        self.assertEqual(leak_guard.check("HEAD", self.repo), (1, [".agentry/memory/secret.db"]))

    def test_leak_is_found_when_run_from_a_subdirectory(self):
        self.write(".agentry/memory/secret.db", "internal")
        self.write("plugin/tools/keep.txt", "x")
        self.commit("plant")
        sub = self.repo / "plugin" / "tools"
        self.assertEqual(leak_guard.check("HEAD", sub), (1, [".agentry/memory/secret.db"]))
        self.assertEqual(leak_guard.check("HEAD", self.repo), (1, [".agentry/memory/secret.db"]))

    def test_option_shaped_ref_is_refused_and_touches_nothing(self):
        victim = self.repo / "victim.txt"
        victim.write_text("precious", encoding="utf-8")
        for ref in ("--output=victim.txt", "-x", "HEAD --output=victim.txt", "HEAD" + chr(10) + "x", ""):
            with self.subTest(ref=ref):
                self.assertEqual(leak_guard.check(ref, self.repo)[0], 2)
        self.assertEqual(victim.read_text(encoding="utf-8"), "precious")


class PrePushHookTest(unittest.TestCase):
    """The real .githooks/pre-push, driven by a real `git push`, with no
    CLAUDE_PROJECT_DIR in the environment: a terminal push has none, and leak_guard
    (via state -> paths) exits 2 without it, so the hook has to name the repo root
    itself."""

    SHIPPED = ("plugin/tools/paths.py", "plugin/tools/pipeline/leak_guard.py",
               "plugin/tools/pipeline/state.py")

    def setUp(self):
        if shutil.which("python") is None or shutil.which("sh") is None:
            self.skipTest("the hook needs python and sh on PATH")
        self.repo = tmproot.sandbox(self, "hook_")
        remote = tmproot.sandbox(self, "hook_remote_")
        git(["init", "-q", "--bare"], remote)
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        git(["config", "core.hooksPath", ".githooks"], self.repo)
        git(["remote", "add", "origin", str(remote)], self.repo)
        for rel in (".githooks/pre-push", *self.SHIPPED):
            target = self.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, target)
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)
        git(["checkout", "-q", "-b", "feature/x"], self.repo)

    def push(self, rel: str) -> subprocess.CompletedProcess:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-q", "-m", "work"], self.repo)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        self.assertNotIn("CLAUDE_PROJECT_DIR", env)
        return subprocess.run(["git", "push", "origin", "feature/x"], cwd=str(self.repo), env=env,
                              capture_output=True, text=True, timeout=60)

    def test_a_clean_push_passes_without_claude_project_dir(self):
        done = self.push("feature.txt")
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_leaking_push_is_refused_by_the_guard_not_by_the_missing_variable(self):
        done = self.push(".agentry/memory/lessons.md")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("project-internal files would be published", done.stderr)
        self.assertNotIn("CLAUDE_PROJECT_DIR is not set", done.stderr)


if __name__ == "__main__":
    unittest.main()

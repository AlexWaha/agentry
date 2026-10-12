"""task-0041 L1: the leak guard in throwaway git repos.

A bare-less origin is not needed: `origin/main` is just a ref, so each case
creates it with `update-ref` after the seed commit.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

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
        self.write(".claude/tools/keep.txt", "x")
        self.commit("plant")
        sub = self.repo / ".claude" / "tools"
        self.assertEqual(leak_guard.check("HEAD", sub), (1, [".agentry/memory/secret.db"]))
        self.assertEqual(leak_guard.check("HEAD", self.repo), (1, [".agentry/memory/secret.db"]))

    def test_option_shaped_ref_is_refused_and_touches_nothing(self):
        victim = self.repo / "victim.txt"
        victim.write_text("precious", encoding="utf-8")
        for ref in ("--output=victim.txt", "-x", "HEAD --output=victim.txt", "HEAD" + chr(10) + "x", ""):
            with self.subTest(ref=ref):
                self.assertEqual(leak_guard.check(ref, self.repo)[0], 2)
        self.assertEqual(victim.read_text(encoding="utf-8"), "precious")


if __name__ == "__main__":
    unittest.main()

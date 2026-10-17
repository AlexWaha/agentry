"""scripts/dashboard_gate.py (FR-118): runs the dashboard npm checks only when dashboard/ changed.

Every case builds a throwaway git repository with a copy of the script in it (the script finds the repo
from its own location) and a fake `npm` first on PATH that records its arguments, so nothing here needs
Node. The fake is a .cmd on Windows and a shell script elsewhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import tmproot

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "dashboard_gate.py"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout


class DashboardGateTest(unittest.TestCase):
    def setUp(self):
        base = tmproot.sandbox(self, "dashgate")
        self.repo = base / "repo"
        self.repo.mkdir()
        (self.repo / "scripts").mkdir()
        shutil.copy(SCRIPT, self.repo / "scripts" / "dashboard_gate.py")
        (self.repo / "dashboard").mkdir()
        (self.repo / "dashboard" / "package.json").write_text("{}\n", encoding="utf-8")
        (self.repo / "dashboard" / "node_modules").mkdir()
        (self.repo / "dashboard" / "node_modules" / "keep.txt").write_text("installed\n", encoding="utf-8")
        (self.repo / "README.md").write_text("repo\n", encoding="utf-8")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "add", "README.md", "scripts", "dashboard/package.json")
        git(self.repo, "commit", "-q", "-m", "base")

        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "npm.log"

    def install_fake_npm(self, exit_code: int = 0) -> None:
        if os.name == "nt":
            body = f'@echo %* >> "{self.log}"\r\n@exit /b {exit_code}\r\n'
            (self.bin / "npm.cmd").write_text(body, encoding="utf-8")
        else:
            body = f'#!/bin/sh\necho "$@" >> "{self.log}"\nexit {exit_code}\n'
            path = self.bin / "npm"
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

    def run_gate(self, stage: str, with_npm: bool = True) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        tool_dirs = [str(Path(shutil.which("git")).parent), str(Path(sys.executable).parent)]
        search = [str(self.bin), *tool_dirs] if with_npm else tool_dirs
        env["PATH"] = os.pathsep.join(search)
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "dashboard_gate.py"), stage],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.repo,
            check=False,
        )

    def npm_calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").split("\n")[:-1] if self.log.exists() else []

    def test_unchanged_dashboard_exits_zero_and_runs_nothing_even_without_node(self):
        done = self.run_gate("test", with_npm=False)

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("unchanged", done.stdout)
        self.assertEqual(self.npm_calls(), [])

    def test_unchanged_dashboard_does_not_call_npm_when_it_exists(self):
        self.install_fake_npm()

        done = self.run_gate("lint")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.npm_calls(), [])

    def test_untracked_file_counts_as_changed_and_runs_the_stage_command(self):
        self.install_fake_npm()
        (self.repo / "dashboard" / "new.ts").write_text("export {};\n", encoding="utf-8")

        done = self.run_gate("typecheck")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([call.strip() for call in self.npm_calls()], ["run typecheck"])

    def test_uncommitted_edit_counts_as_changed(self):
        self.install_fake_npm()
        (self.repo / "dashboard" / "package.json").write_text('{"name": "x"}\n', encoding="utf-8")

        done = self.run_gate("test")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([call.strip() for call in self.npm_calls()], ["run test"])

    def test_commit_on_a_branch_counts_as_changed_against_the_trunk(self):
        self.install_fake_npm()
        git(self.repo, "checkout", "-q", "-b", "feature/x")
        (self.repo / "dashboard" / "src.ts").write_text("export {};\n", encoding="utf-8")
        git(self.repo, "add", "dashboard/src.ts")
        git(self.repo, "commit", "-q", "-m", "dashboard change")

        done = self.run_gate("lint")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([call.strip() for call in self.npm_calls()], ["run lint"])

    def test_change_outside_dashboard_is_not_a_dashboard_change(self):
        self.install_fake_npm()
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")

        done = self.run_gate("test")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.npm_calls(), [])

    def test_installed_packages_do_not_count_as_a_change(self):
        self.install_fake_npm()
        (self.repo / "dashboard" / "node_modules" / "more.txt").write_text("x\n", encoding="utf-8")
        (self.repo / "dashboard" / "coverage").mkdir()
        (self.repo / "dashboard" / "coverage" / "lcov.info").write_text("x\n", encoding="utf-8")

        done = self.run_gate("test")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.npm_calls(), [])

    def test_returns_the_exit_code_of_the_npm_command(self):
        self.install_fake_npm(exit_code=7)
        (self.repo / "dashboard" / "new.ts").write_text("export {};\n", encoding="utf-8")

        done = self.run_gate("test")

        self.assertEqual(done.returncode, 7)

    def test_changed_dashboard_without_node_exits_nonzero_and_names_node(self):
        (self.repo / "dashboard" / "new.ts").write_text("export {};\n", encoding="utf-8")

        done = self.run_gate("test", with_npm=False)

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Node", done.stderr)

    def test_changed_dashboard_without_installed_packages_says_to_run_npm_ci(self):
        self.install_fake_npm()
        shutil.rmtree(self.repo / "dashboard" / "node_modules")
        (self.repo / "dashboard" / "new.ts").write_text("export {};\n", encoding="utf-8")

        done = self.run_gate("test")

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("npm ci", done.stderr)
        self.assertEqual(self.npm_calls(), [])

    def test_unknown_stage_is_a_usage_error(self):
        done = self.run_gate("deploy")

        self.assertEqual(done.returncode, 2)

    def test_repository_without_a_dashboard_folder_passes(self):
        shutil.rmtree(self.repo / "dashboard" / "node_modules")
        git(self.repo, "rm", "-q", "-r", "dashboard")
        git(self.repo, "commit", "-q", "-m", "drop dashboard")

        done = self.run_gate("test", with_npm=False)

        self.assertEqual(done.returncode, 0, done.stderr)

    def test_missing_trunk_is_treated_as_changed(self):
        self.install_fake_npm()
        git(self.repo, "branch", "-m", "main", "other")

        done = self.run_gate("lint")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([call.strip() for call in self.npm_calls()], ["run lint"])

    def test_trunk_name_comes_from_pipeline_json(self):
        self.install_fake_npm()
        git(self.repo, "branch", "-m", "main", "trunk")
        (self.repo / ".agentry").mkdir()
        (self.repo / ".agentry" / "pipeline.json").write_text('{"main_branch": "trunk"}\n', encoding="utf-8")

        done = self.run_gate("test")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.npm_calls(), [])


if __name__ == "__main__":
    unittest.main()

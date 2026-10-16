"""task-0038 S3: the two roots (tools/paths.py) and what hangs off them.

PLUGIN_ROOT is where the code sits; PROJECT_ROOT is CLAUDE_PROJECT_DIR and nothing
else - never a walk up from the cwd, because src/ and every src-wt/<task>/ tree carry
a tracked `.agentry/pipeline.json` a walk would find and write run state beside.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import gate
import pretool_gate
import session_start
import state
import supervisor
import tmproot

sys.path.insert(0, str(TOOLS_DIR / "memory"))
import memory

PYTHON = sys.executable


def run_python(code: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, "-c", code], env=env, cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def env_without_project_dir() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PYTHONPATH"] = str(TOOLS_DIR)
    return env


class RootsTest(unittest.TestCase):

    def test_the_plugin_root_is_where_the_tools_directory_sits(self):
        self.assertEqual(TOOLS_DIR.parent, state.paths.PLUGIN_ROOT)

    def test_the_project_root_is_the_variable_the_suite_set(self):
        self.assertEqual(Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve(), state.paths.PROJECT_ROOT)
        self.assertEqual(state.paths.PROJECT_ROOT, state.ROOT)

    def test_a_child_takes_its_root_from_the_variable_not_from_where_its_script_sits(self):
        root = tmproot.sandbox(self, "paths_root_")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
        done = run_python("import state; print(state.ROOT)", env, TOOLS_DIR / "pipeline")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(str(root.resolve()), done.stdout.strip())

    def test_an_unset_variable_stops_the_project_scripts_with_exit_two_and_a_message(self):
        cwd = tmproot.sandbox(self, "paths_unset_")
        done = run_python(f"import sys; sys.path.insert(0, {str(TOOLS_DIR / 'pipeline')!r}); import state",
                          env_without_project_dir(), cwd)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("CLAUDE_PROJECT_DIR is not set", done.stderr)

    def test_the_cwd_is_never_searched_for_a_project(self):
        cwd = tmproot.sandbox(self, "paths_nowalk_")
        (cwd / ".agentry").mkdir()
        (cwd / ".agentry" / "pipeline.json").write_text("{}", encoding="utf-8")
        done = run_python("import paths; print(paths.PROJECT_ROOT)", env_without_project_dir(), cwd)
        self.assertEqual(2, done.returncode, done.stdout)
        self.assertNotIn(str(cwd), done.stdout)

    def test_a_script_that_needs_only_the_plugin_root_does_not_need_the_variable(self):
        done = run_python("import paths; print(paths.PLUGIN_ROOT)", env_without_project_dir(),
                          tmproot.sandbox(self, "paths_plugin_only_"))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(str(TOOLS_DIR.parent), done.stdout.strip())


class GatePlaceholderTest(unittest.TestCase):

    def run_cmd(self, cmd: str) -> dict:
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": cmd, "expect_exit": 0}}]}}}
        return gate.run_gate(pipeline, "test")

    def test_plugin_is_replaced_by_the_plugin_root(self):
        root = tmproot.sandbox(self, "gate_plugin_")
        (root / "marker.txt").write_text("x", encoding="utf-8")
        with unittest.mock.patch.object(state.paths, "PLUGIN_ROOT", root):
            result = self.run_cmd(f'"{PYTHON}" -c "import pathlib,sys; sys.exit(0 if '
                                  f'pathlib.Path(sys.argv[1], \'marker.txt\').is_file() else 1)" {{plugin}}')
        self.assertTrue(result["passed"], result)
        self.assertIn(root.as_posix(), result["cmd"])

    def test_a_plugin_root_with_a_shell_character_is_refused_like_root(self):
        root = tmproot.sandbox(self, "gate plugin space ")
        with unittest.mock.patch.object(state.paths, "PLUGIN_ROOT", root):
            result = self.run_cmd("echo {plugin}")
        self.assertFalse(result["passed"])
        self.assertIn("refusing {plugin}", result["output"])


class EnvironmentHandoffTest(unittest.TestCase):

    def test_session_start_exports_both_roots_into_the_env_file(self):
        target = tmproot.sandbox(self, "envfile_") / "sessionstart-hook-0.sh"
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_ENV_FILE": str(target)}):
            session_start.export_roots()
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines), lines)
        self.assertTrue(lines[0].startswith("export CLAUDE_PROJECT_DIR="), lines)
        self.assertTrue(lines[1].startswith("export AGENTRY_ROOT="), lines)
        self.assertIn(state.paths.PLUGIN_ROOT.as_posix(), lines[1])

    def test_a_root_with_a_space_is_quoted_for_the_shell(self):
        target = tmproot.sandbox(self, "envfile_") / "env.sh"
        spaced = tmproot.sandbox(self, "plugin with space ")
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_ENV_FILE": str(target)}), \
                unittest.mock.patch.object(state.paths, "PLUGIN_ROOT", spaced):
            session_start.export_roots()
        self.assertIn(f"export AGENTRY_ROOT='{spaced.as_posix()}'", target.read_text(encoding="utf-8"))

    def test_without_an_env_file_there_is_nothing_to_write(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            session_start.export_roots()

    def test_the_suite_never_writes_into_an_inherited_env_file(self):
        """A live session exports its own CLAUDE_ENV_FILE and session_start.main()
        appends to it; tests/__init__.py must clear it (task-0039). The child runs
        a test that drives main() unpatched on that point, with the variable set."""
        target = tmproot.sandbox(self, "envfile_") / "host-session-env.sh"
        target.write_bytes(b"# host session env\n")
        proc = subprocess.run(
            [PYTHON, "-m", "unittest", "discover", "-s", "plugin/tools", "-p", "test_human_output.py",
             "-k", "test_without_a_blocked_run_the_output_is_the_same_plain_text_as_before"],
            cwd=str(TOOLS_DIR.parent.parent), capture_output=True, text=True, timeout=120,
            env={**os.environ, "CLAUDE_ENV_FILE": str(target)})
        out = proc.stdout + proc.stderr
        self.assertIn("Ran 1 test", out, out)
        self.assertEqual(0, proc.returncode, out)
        self.assertEqual(b"# host session env\n", target.read_bytes())

    def test_the_supervisor_names_both_roots_for_a_child_it_starts(self):
        self.assertEqual({"CLAUDE_PROJECT_DIR": str(state.ROOT),
                          "AGENTRY_ROOT": state.paths.PLUGIN_ROOT.as_posix()},
                         supervisor.child_env())

    def test_the_relaunch_prompt_names_the_script_through_the_plugin_root(self):
        self.assertIn('python "$AGENTRY_ROOT/tools/pipeline/advance.py"', supervisor.RELAUNCH_PROMPT)


class GateRecognisesThePluginRootTest(unittest.TestCase):

    def test_a_harness_script_is_allowed_through_agentry_root_for_a_readonly_agent(self):
        for command in ('python "$AGENTRY_ROOT/tools/memory/memory.py" --stats',
                        'python $AGENTRY_ROOT/tools/pipeline/state.py --show',
                        'python "${AGENTRY_ROOT}/tools/pipeline/events.py" --summary'):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.program_denied(command, "readonly"))

    def test_a_script_outside_the_plugin_tools_is_refused(self):
        for command in ('python "$AGENTRY_ROOT/../evil.py"', "python docs/memory.py --stats"):
            with self.subTest(command=command):
                self.assertNotEqual("", agent_gate.program_denied(command, "readonly"))

    def test_a_command_that_assigns_the_variable_gets_no_expansion(self):
        command = 'AGENTRY_ROOT=/tmp/x python "$AGENTRY_ROOT/tools/memory/memory.py" --stats'
        self.assertNotEqual("", agent_gate.program_denied(command, "readonly"))

    def test_the_plugin_root_is_the_live_harness_whatever_the_project_root(self):
        root = tmproot.sandbox(self, "live_plugin_")
        (root / "tools").mkdir()
        with unittest.mock.patch.object(state.paths, "PLUGIN_ROOT", root):
            self.assertTrue(pretool_gate.is_live_harness(str(root / "tools" / "x.py")))
            self.assertTrue(pretool_gate.is_live_harness(str(root / "hooks" / "hooks.json")))
            self.assertFalse(pretool_gate.is_live_harness(str(root.parent / "elsewhere.py")))

    def test_memory_export_may_not_target_the_plugin_root(self):
        root = tmproot.sandbox(self, "memory_plugin_")
        with unittest.mock.patch.object(state.paths, "PLUGIN_ROOT", root):
            self.assertEqual("inside the plugin root", memory._protected_target(str(root / "tools" / "x.md")))
            self.assertEqual("", memory._protected_target(str(root.parent / "ok.md")))


class DocsProfileSkillsTest(unittest.TestCase):

    def test_plugin_skills_are_documents_a_docs_agent_may_write(self):
        self.assertTrue(agent_gate.is_docs_path("plugin/skills/new-task/SKILL.md"))
        self.assertTrue(agent_gate.is_docs_path("src/plugin/skills/new-task/SKILL.md"))
        self.assertFalse(agent_gate.is_docs_path("plugin/tools/pipeline/state.py"))


if __name__ == "__main__":
    unittest.main()

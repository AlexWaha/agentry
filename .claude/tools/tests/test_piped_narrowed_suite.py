"""A narrowed unittest or pytest run piped into a filter is denied too (task-0123).

task-0082 denied the unfiltered suite piped into head/tail/grep/wc/more/less. The hazard
(a test child inherits the pipe's write end, the reader never sees EOF) applies to any run
that spawns children, narrowed or not; in the task-0120 swarm four agents piped narrowed
runs into `tail`, one of them five times after being told not to.

Every denied shape is driven through the orchestrator's Bash gate and through the dev,
readonly and docs profile handlers, so the one function both gates call is exercised from
all four entry points.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import agent_gate
import pretool_gate

SINKS = ("head", "tail", "grep", "wc", "more", "less")

# (label, command without the pipe): every one is a unittest or pytest run.
DENIED_SHAPES = (
    ("module list", "python -m unittest test_a test_b"),
    ("dotted path", "python -m unittest tests.test_pretool_gate"),
    ("test id", "python -m unittest tests.test_pretool_gate.PipedTestSuiteGateTest.test_x"),
    ("-k without discover", "python -m unittest -k piped tests.test_pretool_gate"),
    ("-k with discover", "python -m unittest discover -s tests -k piped"),
    ("discover -p", "python -m unittest discover -s tests -p test_x.py"),
    ("-v with a module", "python -m unittest -v tests.test_x"),
    ("pytest test id", "pytest -x tests/test_x.py::Case::test_y"),
    ("bare pytest", "pytest tests"),
    ("pytest.exe", "pytest.exe tests"),
    ("python -m pytest -k", "python -m pytest -k piped tests"),
    ("python3.12", "python3.12 -m unittest tests.test_x"),
    ("py launcher", "py -m unittest tests.test_x"),
    ("python.exe", "python.exe -m unittest tests.test_x"),
    ("windows path", "C:/Python312/python.exe -m unittest tests.test_x"),
    ("flags before -m", "python -I -m unittest tests.test_x"),
    ("glued -m", "python -munittest tests.test_x"),
    ("glued -m pytest", "python -mpytest tests"),
    ("flag cluster", "python -Im unittest tests.test_x"),
    ("submodule", "python -m unittest.mock tests.test_x"),
    ("env prefix", "PYTHONPATH=. python -m unittest tests.test_x"),
    ("env wrapper", "env X=1 python -m unittest tests.test_x"),
    ("uv run", "uv run python -m unittest tests.test_x"),
    ("timeout wrapper", "timeout 60 pytest tests"),
    ("brace group", "{ python -m unittest tests.test_x; }"),
    ("after cd", "cd .claude/tools && python -m unittest tests.test_x"),
    ("upper case", "PYTHON -M UNITTEST tests.test_x"),
)
# separator spellings between the run and the sink
PIPES = ("|", "2>&1 |", "|&")

ALLOWED = (
    "python -m unittest tests.test_x",
    "python -m unittest discover -s tests -p test_x.py",
    "pytest -x tests/test_x.py",
    "git log | head",
    "git log --oneline | tail -5",
    "python -m pip list | head",
    "python -m json.tool data.json | head",
    "python script.py | tail",
    "python -m unittest tests.test_x | python summarize.py",
    "python -m unittest tests.test_x | tee tmp/log.txt",
    "pytest tests | cat",
    "cat tests/test_x.py | grep unittest",
    "git log -m unittest | head",
    "grep -r pytest . | wc -l",
    "ls | wc -l",
)


def with_flag(flag: bool = True):
    cfg = dict(pretool_gate.gates_cfg())
    cfg["forbid_piped_test_suite"] = flag
    return unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg)


def stderr_of(call, *args, **kwargs) -> tuple:
    err = io.StringIO()
    with contextlib.redirect_stderr(err), unittest.mock.patch("sys.stderr", err):
        code = call(*args, **kwargs)
    return code, err.getvalue()


def orchestrator(command: str) -> tuple:
    return stderr_of(pretool_gate.handle_bash, command, cwd=".")


def dev(command: str) -> tuple:
    # The full-suite guard of the dev profile is a different check with its own tests;
    # it is switched off here so only the piped-suite check can be the cause of a deny.
    with unittest.mock.patch.object(agent_gate, "unnarrowed_test_cmd", return_value=""):
        return stderr_of(agent_gate.handle_dev, "Bash", {"command": command}, cwd=".")


def readonly(command: str) -> tuple:
    return stderr_of(agent_gate.lexical_bash_checks, "readonly", command, ".")


def docs(command: str) -> tuple:
    return stderr_of(agent_gate.lexical_bash_checks, "docs", command, ".")


ENTRY_POINTS = (("orchestrator", orchestrator), ("dev", dev), ("readonly", readonly),
                ("docs", docs))


class NarrowedRunsPipedIntoAFilterAreDeniedTest(unittest.TestCase):
    def test_every_shape_into_every_sink_is_denied_everywhere_with_the_capture_text(self):
        for label, run in DENIED_SHAPES:
            for sink in SINKS:
                command = f"{run} | {sink} -20"
                for entry, call in ENTRY_POINTS:
                    with self.subTest(shape=label, sink=sink, entry=entry), with_flag():
                        code, msg = call(command)
                        self.assertEqual(2, code, msg)
                        self.assertIn("forbid_piped_test_suite", msg)
                        self.assertIn(f"'{sink}'", msg)
                        self.assertIn("unpiped", msg)
                        self.assertIn("out=$(cmd 2>&1)", msg)
                        self.assertIn("tmp/", msg)

    def test_the_stderr_pipe_spellings_are_denied(self):
        for pipe in PIPES:
            command = f"python -m unittest tests.test_x {pipe} tail -20"
            for entry, call in ENTRY_POINTS:
                with self.subTest(pipe=pipe, entry=entry), with_flag():
                    code, msg = call(command)
                    self.assertEqual(2, code, msg)
                    self.assertIn("forbid_piped_test_suite", msg)

    def test_the_sink_in_the_middle_of_a_longer_chain_is_denied(self):
        command = "python -m unittest tests.test_x 2>&1 | grep FAIL | wc -l"
        with with_flag():
            code, msg = orchestrator(command)
        self.assertEqual(2, code, msg)
        self.assertIn("'grep'", msg)

    def test_the_message_names_unittest_and_pytest_not_only_discover(self):
        with with_flag():
            _, msg = orchestrator("python -m unittest tests.test_x | tail")
        self.assertIn("(unittest / pytest)", msg)
        self.assertNotIn("discover", msg)

    def test_the_sink_name_is_what_piped_test_suite_sink_returns(self):
        samples = {
            "python -m unittest tests.test_x | tail": "tail",
            "pytest -k x | head -5": "head",
            "python -m pytest tests 2>&1 | grep ok": "grep",
            "python -munittest a | wc -l": "wc",
            "py -m unittest a | less": "less",
            "python -m unittest a | more": "more",
        }
        for command, sink in samples.items():
            with self.subTest(command=command):
                self.assertEqual(sink, pretool_gate.piped_test_suite_sink(command))

    def test_a_script_argument_spelled_dash_m_unittest_is_denied_too(self):
        # Accepted corner, fail-closed: any -m counts, as the old pytest rule did.
        with with_flag():
            code, _ = orchestrator("python script.py -m unittest | tail")
        self.assertEqual(2, code)


class OtherCommandsStayAllowedTest(unittest.TestCase):
    def test_unpiped_runs_and_non_test_pipes_are_allowed_at_the_check(self):
        for command in ALLOWED:
            with self.subTest(command=command), with_flag():
                self.assertEqual("", pretool_gate.piped_test_suite_sink(command))
                self.assertEqual(0, pretool_gate.check_piped_test_suite(command))

    def test_unpiped_runs_and_non_test_pipes_are_allowed_by_the_orchestrator_gate(self):
        for command in ALLOWED:
            with self.subTest(command=command), with_flag():
                code, msg = orchestrator(command)
                self.assertNotIn("forbid_piped_test_suite", msg)
                self.assertEqual(0, code, msg)

    def test_a_narrowed_run_piped_into_a_non_sink_is_allowed_by_every_entry_point(self):
        command = "python -m unittest tests.test_x | python summarize.py"
        for entry, call in ENTRY_POINTS:
            with self.subTest(entry=entry), with_flag():
                self.assertNotIn("forbid_piped_test_suite", call(command)[1])

    def test_with_the_flag_off_nothing_is_denied(self):
        for label, run in DENIED_SHAPES:
            with self.subTest(shape=label), with_flag(False):
                self.assertEqual(0, pretool_gate.check_piped_test_suite(f"{run} | tail"))

    def test_text_that_cannot_be_tokenised_fails_open_here(self):
        with with_flag():
            self.assertEqual("", pretool_gate.piped_test_suite_sink("python -m unittest 'a | tail"))


class SegmentRecognitionTest(unittest.TestCase):
    def test_runs_test_module_reads_the_module_after_dash_m(self):
        yes = (["python", "-m", "unittest"], ["python", "-m", "pytest", "x"],
               ["python", "-munittest"], ["python", "-im", "unittest"],
               ["python", "-m", "unittest.main"])
        no = (["python", "-m", "pip", "list"], ["python", "-m"], ["python", "unittest"],
              ["python", "--timeout", "unittest"], ["python", "-m", "json.tool"])
        for argv in yes:
            with self.subTest(argv=argv):
                self.assertTrue(pretool_gate.runs_test_module(argv))
        for argv in no:
            with self.subTest(argv=argv):
                self.assertFalse(pretool_gate.runs_test_module(argv))


if __name__ == "__main__":
    unittest.main()

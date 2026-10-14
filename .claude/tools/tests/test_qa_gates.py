"""QA coverage for task-0125 (shared abbreviation helper, shared Bash prelude) and task-0123
(narrowed unittest / pytest runs piped into a filter), added on top of the developer's
test_gate_prelude and test_piped_narrowed_suite.

What this file adds:
- the refactored git option abbreviation compared with the pre-refactor body, token by token;
- every prelude cause in several spellings, through the real profile handlers;
- the false-positive list for the widened pipe matcher (commands that must stay allowed);
- more denied shapes (interpreter flags, quoting, wrappers, sink spellings);
- the fix round: abbreviated option spellings (`--wai`, `sed --i`), the pipe matcher inside
  nested bodies and across a whole pipe chain, and wrappers that take an option value.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import itertools
import shlex
import sys
import unittest
import unittest.mock
from pathlib import Path
from typing import ClassVar

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import agent_gate
import pretool_gate
import state

ALLOWED = 0
DENIED = 2
CWD = str(state.ROOT)
PIPE_FLAG = "forbid_piped_test_suite"

DEV = "Dev agent"
READONLY = "Read-only agent"
DOCS = "Docs agent"
PROFILES = (DEV, READONLY, DOCS)


def stderr_of(call, *args, **kwargs) -> tuple:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = call(*args, **kwargs)
    return code, err.getvalue()


def profile_bash(who: str, command: str) -> tuple:
    """One Bash call through the whole profile handler, as the hook reaches it. The dev
    profile's full-suite guard is a different check, so it is switched off here."""
    ti = {"command": command}
    if who == DEV:
        with unittest.mock.patch.object(agent_gate, "unnarrowed_test_cmd", return_value=""):
            return stderr_of(agent_gate.handle_dev, "Bash", ti, CWD)
    handler = agent_gate.handle_readonly if who == READONLY else agent_gate.handle_docs
    return stderr_of(handler, "Bash", ti, CWD)


def with_flag(flag: bool = True):
    cfg = dict(pretool_gate.gates_cfg())
    cfg[PIPE_FLAG] = flag
    return unittest.mock.patch.object(pretool_gate, "gates_cfg", return_value=cfg)


class AbbreviationMatchesThePreRefactorBodyTest(unittest.TestCase):
    """push_option_in and agent_gate's old is_long_option_prefix, re-stated verbatim from
    main, against the shared helper over every prefix of every option the gates know."""

    OPTIONS = sorted(set(pretool_gate.PUSH_FANOUT_LONG) | {
        "--target-directory", "--suffix", "--sparse", "--no-preserve", "--force",
        "--force-with-lease", "--delete", "--prune", "--mirror"})

    @staticmethod
    def old_push_option_in(tok: str, names) -> str:
        name = tok.split("=", 1)[0]
        if len(name) < 3 or not name.startswith("--"):
            return ""
        return next((full for full in sorted(names) if full.startswith(name)), "")

    @staticmethod
    def old_is_long_option_prefix(name: str, option: str) -> bool:
        return len(name) > 2 and option.startswith(name)

    def names(self) -> list:
        grid = {"", "-", "--", "---", "-a", "-t", "main", "--=x", "--a=b", "a=--all",
                "--all=1", "--ALL", "--Al", "-----"}
        for option in self.OPTIONS:
            for end in range(len(option) + 1):
                grid.add(option[:end])
                grid.add(option[:end] + "x")
        return sorted(grid)

    def test_push_option_in_equals_the_old_body_for_every_token(self):
        fanout = pretool_gate.PUSH_FANOUT_LONG
        for name in self.names():
            for token in (name, name + "=value"):
                with self.subTest(token=token):
                    self.assertEqual(self.old_push_option_in(token, fanout),
                                     pretool_gate.push_option_in(token, fanout))

    def test_the_helper_equals_the_old_agent_gate_function_for_every_pair(self):
        for name in self.names():
            for option in self.OPTIONS:
                with self.subTest(name=name, option=option):
                    self.assertEqual(self.old_is_long_option_prefix(name, option),
                                     pretool_gate.is_long_option_prefix(name, option))

    def test_a_two_character_name_abbreviates_nothing(self):
        self.assertFalse(pretool_gate.is_long_option_prefix("--", "--all"))
        self.assertFalse(pretool_gate.is_long_option_prefix("-a", "--all"))

    def test_a_three_character_name_is_the_shortest_abbreviation(self):
        self.assertTrue(pretool_gate.is_long_option_prefix("--a", "--all"))
        self.assertFalse(pretool_gate.is_long_option_prefix("--b", "--all"))

    def test_cp_target_directory_abbreviations_reach_the_protected_state_check(self):
        for who in PROFILES:
            for command in ("cp --target=.agentry/state a", "cp --t=.agentry/state a",
                            "cp --target-directory=.agentry/state a", "cp -t .agentry/state a"):
                with self.subTest(who=who, command=command):
                    code, msg = profile_bash(who, command)
                    self.assertEqual(DENIED, code, msg)
                    self.assertIn(".agentry/state", msg)
                    self.assertIn(who, msg)

    def test_cp_names_that_only_look_like_the_long_option_are_not_a_target_directory(self):
        # `--targets=x` is not a prefix of --target-directory and a bare `--` ends options
        for command in ("cp --targets=.agentry/state a b", "cp -- a b"):
            with self.subTest(command=command):
                self.assertEqual((ALLOWED, ""), profile_bash(DEV, command))


class PreludeCausesInEverySpellingTest(unittest.TestCase):
    A = "python .claude/tools/pipeline/approve.py"
    H = "python .claude/tools/pipeline/handoff.py"
    M = "python .claude/tools/memory/memory.py"

    CAUSES: ClassVar[dict] = {
        "approve": (
            f"{A} --task task-0001 --gate plan",
            f"env {A} --task task-0001",
            "python -m pipeline.approve --task task-0001",
            "cd .claude && python tools/pipeline/approve.py --task x",
            f"echo hi && {A} --task x",
            f"bash -c '{A} --task x'",
        ),
        "waive": (
            f"{H} --waive task-0001 --reason x",
            f"echo hi && {H} --waive task-0001 --reason x",
        ),
        "setter": (
            "python .claude/tools/pipeline/approvals.py --set auto",
            "python .claude/tools/pipeline/mode.py --set solo",
            "python .claude/tools/pipeline/swarm.py on",
            "python .claude/tools/pipeline/zones.py --prove --task task-0001",
            "python .claude/tools/pipeline/repo_scan.py --write",
        ),
        "protected": (
            "echo x > .agentry/state/approvals",
            "echo x >> .agentry/pipeline.json",
            "printf x | tee .agentry/state/mode",
            "cp tmp/x .agentry/state/zones.json",
            "sed -i s/a/b/ .agentry/pipeline.json",
        ),
        "memory": (
            f"{M} --export --out /etc/x.json",
            f"{M} --export --out .claude/tools/pipeline/pretool_gate.py",
            f"{M} --db /tmp/evil.db --stats",
        ),
    }
    HEADLINE: ClassVar[dict] = {
        "approve": "approve.py is orchestrator-only.",
        "waive": "handoff.py --waive is orchestrator-only",
        "setter": "orchestrator-only when it SETS",
        "protected": "is under .agentry/state/",
        "memory": "is not under .agentry/memory/ or tmp/",
    }
    CLEAN = ("python .claude/tools/pipeline/approvals.py --show",
             "python .claude/tools/pipeline/repo_scan.py",
             f"{H} --check",
             f"{M} --query foo",
             f"{M} --export --out tmp/ok.md",
             "cat .agentry/state/approvals",
             "git status", "ls -la")

    def test_each_cause_in_each_spelling_is_denied_by_every_profile_with_its_headline(self):
        for cause, commands in self.CAUSES.items():
            for command in commands:
                for who in PROFILES:
                    with self.subTest(cause=cause, who=who, command=command):
                        code, msg = profile_bash(who, command)
                        self.assertEqual(DENIED, code, msg)
                        self.assertIn(self.HEADLINE[cause], msg)

    def test_two_causes_in_one_command_name_the_earlier_one_in_the_documented_order(self):
        # approve.py, handoff --waive, a state setter, a protected write, a memory target
        order = ("approve", "waive", "setter", "protected", "memory")
        for earlier, later in itertools.pairwise(order):
            for who in PROFILES:
                command = f"{self.CAUSES[later][0]} && {self.CAUSES[earlier][0]}"
                with self.subTest(earlier=earlier, later=later, who=who):
                    _, msg = profile_bash(who, command)
                    self.assertIn(self.HEADLINE[earlier], msg)

    def test_the_dev_advice_tail_is_for_dev_alone(self):
        for who in PROFILES:
            _, approve = profile_bash(who, self.CAUSES["approve"][0])
            _, waive = profile_bash(who, self.CAUSES["waive"][0])
            with self.subTest(who=who):
                self.assertEqual(who == DEV, "self-approval" in approve)
                self.assertEqual(who == DEV, "Report the handoff problem" in waive)

    def test_the_protected_and_memory_texts_carry_the_label_of_the_profile_asking(self):
        for cause in ("protected", "memory"):
            for who in PROFILES:
                with self.subTest(cause=cause, who=who):
                    _, msg = profile_bash(who, self.CAUSES[cause][0])
                    self.assertTrue(msg.startswith(f"{who}:"), msg)

    def test_the_read_only_forms_of_the_same_scripts_pass_the_prelude(self):
        for command in self.CLEAN:
            for who in PROFILES:
                with self.subTest(who=who, command=command):
                    self.assertEqual(ALLOWED, agent_gate.bash_prelude(who, command, CWD))

    def test_a_cause_that_the_prelude_does_not_own_does_not_come_back_from_it(self):
        # mutating Bash and an unparseable command are decided after the prelude
        for command in ("echo x > out.txt", "rm -rf build", "mkdir foo"):
            with self.subTest(command=command):
                self.assertEqual(ALLOWED, agent_gate.bash_prelude(READONLY, command, CWD))


class PreludeOrderAgainstTheSuiteGuardTest(unittest.TestCase):
    """readonly and docs ran: approve, waive, setter, piped suite, protected write, memory
    target. bash_prelude puts the piped-suite check after the whole prelude. A command with
    two causes may therefore name the other cause first for these two profiles: accepted by
    the User in the plan (D1), pinned here as the behaviour (finding QA-G1)."""

    PIPED = "python -m unittest discover -s tests | tail"
    MEMORY = "python .claude/tools/memory/memory.py --export --out /etc/x.json"
    BOTH = f"{PIPED} && {MEMORY}"

    def test_a_command_with_both_causes_is_still_denied_by_both_restricted_profiles(self):
        for who in (READONLY, DOCS):
            with self.subTest(who=who), with_flag():
                code, msg = profile_bash(who, self.BOTH)
                self.assertEqual(DENIED, code, msg)

    def test_a_command_with_both_causes_names_the_prelude_cause_first(self):
        for who in (READONLY, DOCS):
            with self.subTest(who=who), with_flag():
                _, msg = profile_bash(who, self.BOTH)
                self.assertIn("memory.py --out", msg)
                self.assertNotIn(PIPE_FLAG, msg)

    def test_each_cause_alone_is_named_by_its_own_text(self):
        for who in (READONLY, DOCS):
            with self.subTest(who=who), with_flag():
                self.assertIn(PIPE_FLAG, profile_bash(who, self.PIPED)[1])
                self.assertIn("memory.py --out", profile_bash(who, self.MEMORY)[1])


class WidenedMatcherKeepsOrdinaryPipesAllowedTest(unittest.TestCase):
    """False-positive probe: none of these is a unittest or pytest run, so a sink after it is
    nobody's deadlock."""

    NOT_A_TEST_RUN = (
        "python -m pip install pytest | tail",
        "python -m pip install unittest-xml-reporting | tail",
        "python -c 'import unittest' | tail",
        "python -c 'print(1)' | wc -l",
        "python script.py --message unittest | tail",
        "python script.py -n pytest | tail",
        "python script.py unittest | tail",
        "python build.py --module pytest | tail",
        "python -m http.server | head",
        "python -m doctest x.py | tail",
        "python -m timeit -n 3 x | tail",
        "python -m venv .v | tail",
        "python -m mypy x | head",
        "python -m ruff check . | head",
        "python -m unittest_foo | tail",
        "python -m pytest_cov | tail",
        "python -m pytestx | tail",
        "python -mfoo unittest | tail",
        "python -Wignore script.py -m | tail",
        "ruff check .claude/tools | tail",
        "pip show pytest | head",
        "which pytest | head",
        "pytest-cov --version | head",
        "git commit -m unittest | tail",
        "git log --grep=pytest | head",
        "echo python -m unittest x | head",
        "grep -rn 'python -m unittest' docs | head",
        "ls tests | grep pytest",
    )
    NO_PIPE_INTO_A_SINK = (
        "python -m unittest tests.test_x > tmp/out.log 2>&1",
        "python -m unittest tests.test_x 2>&1 > tmp/o.log",
        "out=$(python -m unittest tests.test_x 2>&1)",
        "python -m unittest tests.test_x | cat",
        "python -m unittest tests.test_x | tee tmp/log.txt",
        "python -m unittest tests.test_x | python summarize.py",
        "python -m unittest tests.x; tail log",
        "python -m unittest tests.x && tail log",
        "tail log | python -m unittest tests.x",
        "tail log | pytest tests",
    )

    def test_no_sink_is_reported_for_any_of_them(self):
        for command in self.NOT_A_TEST_RUN + self.NO_PIPE_INTO_A_SINK:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.piped_test_suite_sink(command))

    def test_the_check_allows_them_with_the_flag_on(self):
        for command in self.NOT_A_TEST_RUN + self.NO_PIPE_INTO_A_SINK:
            with self.subTest(command=command), with_flag():
                self.assertEqual(ALLOWED, pretool_gate.check_piped_test_suite(command))

    def test_the_restricted_profiles_do_not_name_the_pipe_rule_for_them(self):
        for command in self.NOT_A_TEST_RUN + self.NO_PIPE_INTO_A_SINK:
            for who in (READONLY, DOCS):
                with self.subTest(who=who, command=command), with_flag():
                    self.assertNotIn(PIPE_FLAG, profile_bash(who, command)[1])


class MoreDeniedShapesTest(unittest.TestCase):
    """(command, sink) pairs the developer's table does not carry."""

    DENIED_CASES = (
        ("python -u -m unittest tests.x | tail", "tail"),
        ("python -X utf8 -m unittest tests.x | tail", "tail"),
        ("python -W ignore -m pytest tests | tail", "tail"),
        ("python -m  unittest tests.x | tail", "tail"),
        ("python  -m\tunittest tests.x | tail", "tail"),
        ("python -m 'unittest' tests.x | tail", "tail"),
        ('python -m "pytest" tests | tail', "tail"),
        ("'python' -m unittest tests.x | tail", "tail"),
        ("\\python -m unittest tests.x | tail", "tail"),
        ("/usr/bin/python3 -m pytest | tail", "tail"),
        ("python -m unittest.main tests.x | tail", "tail"),
        ("python -m unittest -k a -k b tests.x | tail", "tail"),
        ("python -m pytest tests -x -q --tb=short | tail", "tail"),
        ("python -m pytest | wc", "wc"),
        ("python -m coverage run -m unittest tests.x | tail", "tail"),
        ("uv run pytest | tail", "tail"),
        ("pipenv run python -m unittest tests.x | tail", "tail"),
        ("(python -m unittest tests.x) | tail", "tail"),
        ("python -m unittest tests.x |tail", "tail"),
        ("python -m unittest tests.x 2>&1| tail", "tail"),
        ("python -m unittest tests.x |& tail", "tail"),
        ("python -m unittest tests.x | /usr/bin/tail -5", "tail"),
        ("python -m unittest tests.x | TAIL", "tail"),
        ("python -m unittest tests.x | command tail", "tail"),
        ("python -m unittest tests.x | env tail", "tail"),
        ("python -m unittest tests.x | head -1 | tail", "head"),
        ("python -m unittest tests.x | grep FAIL | wc -l", "grep"),
    )
    # Decided, not accidental: help and version output never spawns a child, but the matcher
    # reads spelling only, and these were already denied for pytest before task-0123.
    HARMLESS_BUT_DENIED = (
        "pytest --version | head",
        "pytest --help | head",
        "python -m unittest --help | head",
        "python -m unittest -h | tail",
        "python -m pytest --collect-only | head",
    )

    def test_sink_name_for_each_shape(self):
        for command, sink in self.DENIED_CASES:
            with self.subTest(command=command):
                self.assertEqual(sink, pretool_gate.piped_test_suite_sink(command))

    def test_every_shape_is_denied_by_every_restricted_profile_and_by_dev(self):
        for command, sink in self.DENIED_CASES:
            for who in PROFILES:
                with self.subTest(who=who, command=command), with_flag():
                    code, msg = profile_bash(who, command)
                    self.assertEqual(DENIED, code, msg)
                    self.assertIn(PIPE_FLAG, msg)
                    self.assertIn(f"'{sink}'", msg)

    def test_help_and_version_runs_are_denied_too(self):
        for command in self.HARMLESS_BUT_DENIED:
            with self.subTest(command=command):
                self.assertNotEqual("", pretool_gate.piped_test_suite_sink(command))

    def test_with_the_flag_off_every_shape_passes_the_check(self):
        for command, _ in self.DENIED_CASES:
            with self.subTest(command=command), with_flag(False):
                self.assertEqual(ALLOWED, pretool_gate.check_piped_test_suite(command))


class AbbreviatedWaiveFlagTest(unittest.TestCase):
    """Finding QA-G4 (High, present on main before task-0125): handoff.py takes its options
    through argparse, which accepts any unambiguous prefix, so `--wai TASK` records a waiver
    exactly like `--waive TASK`. runs_waive used to read the literal token only, and handoff.py
    is on the readonly and docs allowlists, so every profile could record a User waiver this
    way. It now asks the shared is_long_option_prefix."""

    H = "python .claude/tools/pipeline/handoff.py"

    def test_the_full_flag_is_read_as_a_waiver(self):
        self.assertTrue(agent_gate.runs_waive(f"{self.H} --waive task-0001 --reason x"))

    def test_an_abbreviation_of_the_flag_is_read_as_a_waiver(self):
        for flag in ("--w", "--wa", "--wai", "--waiv", "--waiv=task-0001", "--waive=task-0001"):
            with self.subTest(flag=flag):
                self.assertTrue(agent_gate.runs_waive(f"{self.H} {flag} task-0001 --reason x"))

    def test_an_abbreviation_inside_a_nested_shell_or_substitution_is_read_too(self):
        for command in (f"bash -c '{self.H} --wai task-0001 --reason x'",
                        f"echo $({self.H} --waiv task-0001 --reason x)",
                        f"env -u FOO {self.H} --wa task-0001 --reason x"):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.runs_waive(command))

    def test_every_profile_denies_the_abbreviated_flag(self):
        for who in PROFILES:
            with self.subTest(who=who):
                code, _ = profile_bash(who, f"{self.H} --wai task-0001 --reason x")
                self.assertEqual(DENIED, code)

    def test_the_other_handoff_options_are_not_waivers(self):
        for flag in ("--check", "--for task-0001", "--force", "--fo task-0001", "--reason x",
                     "--inject", "--wr", "--waivers"):
            with self.subTest(flag=flag):
                self.assertFalse(agent_gate.runs_waive(f"{self.H} {flag}"))

    def test_the_flag_on_another_script_is_not_a_waiver(self):
        self.assertFalse(agent_gate.runs_waive("python tools/other.py --waive task-0001"))
        self.assertFalse(agent_gate.runs_waive("git log --wai"))


class OtherLiteralOptionMatchesTest(unittest.TestCase):
    """The audit of finding QA-G4 over the rest of the Bash prelude: every option a gate keys
    on is matched with the abbreviation rule of the program that parses it."""

    def test_sed_in_place_is_read_in_every_abbreviation(self):
        for opt in ("-i", "-i.bak", "-ni", "--in-place", "--in-place=.bak", "--i", "--in",
                    "--in-pl=.bak"):
            with self.subTest(opt=opt):
                self.assertTrue(agent_gate.in_place_sed([opt, "s/a/b/", "f"]))

    def test_other_sed_options_are_not_in_place(self):
        for opt in ("-n", "--quiet", "--expression=p", "--regexp-extended", "--"):
            with self.subTest(opt=opt):
                self.assertFalse(agent_gate.in_place_sed([opt, "p", "f"]))

    def test_an_abbreviated_in_place_is_a_mutation_for_a_readonly_agent(self):
        for command in ("sed --i s/a/b/ f", "sed --in=.bak s/a/b/ f", "sed -n --in s/a/b/ f"):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.bash_mutates(command))
                code, _ = profile_bash(READONLY, command)
                self.assertEqual(DENIED, code)

    def test_sed_files_keeps_the_file_operand_when_the_script_option_is_abbreviated(self):
        for args in (["-i", "--exp=1d", "f"], ["-i", "--expr", "1d", "f"],
                     ["-i", "--fi=x.sed", "f"], ["-i", "--expression=1d", "f"],
                     ["-i", "-e", "1d", "f"], ["-i", "-e1d", "f"], ["-i", "1d", "f"]):
            with self.subTest(args=args):
                self.assertEqual(["f"], agent_gate.sed_files(args))

    def test_an_abbreviated_script_option_does_not_hide_a_protected_file_from_the_dev_check(self):
        command = "sed -i --exp=1d .agentry/pipeline.json"
        self.assertEqual(DENIED, profile_bash(DEV, command)[0])

    def test_symbolic_ref_delete_is_read_in_every_abbreviation(self):
        for flag in ("-d", "--delete", "--del", "--de"):
            with self.subTest(flag=flag):
                self.assertTrue(agent_gate.symbolic_ref_writes_remote_ref(
                    [flag, "refs/remotes/origin/HEAD"]))
        self.assertFalse(agent_gate.symbolic_ref_writes_remote_ref(["refs/remotes/origin/HEAD"]))
        self.assertFalse(agent_gate.symbolic_ref_writes_remote_ref(["--short", "HEAD"]))

    def test_every_dev_label_in_the_agent_gate_is_the_one_constant(self):
        self.assertEqual(DEV, agent_gate.DEV_WHO)
        self.assertEqual(DEV, agent_gate.PROFILE_LABELS["dev"])
        self.assertEqual({agent_gate.DEV_WHO}, set(agent_gate.PRELUDE_TAILS))


def nest(command: str, levels: int) -> str:
    """`command` wrapped in `levels` nested `bash -c` shells."""
    for _ in range(levels):
        command = f"bash -c {shlex.quote(command)}"
    return command


class ShapesTheMatcherSeesInNestedBodiesTest(unittest.TestCase):
    """Finding QA-G2 (Medium): a test run inside `$( )`, a backtick, `bash -c` or an `eval`
    has the same pipe hazard as one at the top level, and `$(python -m unittest x | tail)` is
    the shape an agent reaches for when told to capture. Also the whole pipe chain (`| tee
    f | tail`, `{ run; } | tail`) and wrappers that take an option value before the command."""

    DENIED_SHAPES: ClassVar[tuple] = (
        ("out=$(python -m unittest tests.x 2>&1 | tail -5)", "tail"),
        ("echo $(pytest | tail)", "tail"),
        ("out=`pytest -q | tail -3`", "tail"),
        ("bash -c 'python -m unittest tests.x | tail'", "tail"),
        ('bash -c "pytest | head"', "head"),
        ("eval 'pytest | tail'", "tail"),
        ("python -m unittest tests.x | tee tmp/l | tail", "tail"),
        ("python -m unittest tests.x 2>&1 | cat | grep OK", "grep"),
        ("{ python -m unittest x; } | tail", "tail"),
        ("coverage run -m unittest tests.x | tail", "tail"),
        ("coverage run --source=x -m pytest | head", "head"),
        ("coverage3 run -m unittest | wc -l", "wc"),
        ("py.test | tail", "tail"),
        ("timeout -s KILL 60 python -m unittest x | tail", "tail"),
        ("timeout --signal=KILL 60 pytest | tail", "tail"),
        ("env -u FOO python -m pytest | tail", "tail"),
        ("sudo -u root pytest | head", "head"),
        ("uv run --with pkg python -m unittest x | tail", "tail"),
        ("uv run --with pkg pytest | tail", "tail"),
        ("echo $(bash -c 'pytest | tail')", "tail"),
    )

    ALLOWED_SHAPES: ClassVar[tuple] = (
        "out=$(git log | tail -5)",
        "echo $(ls | head)",
        "bash -c 'git log | tail'",
        "eval 'ls | tail'",
        'out=$(python -m unittest tests.x 2>&1); echo "$out" | tail',
        "out=$(pytest -q) && echo $out | tail -3",
        "pytest > tmp/l; tail tmp/l",
        "pytest; ls | tail",
        "echo $(pytest) | cat",
        "python -m pip list | tee tmp/l | tail",
        "xargs grep pytest | head",
        "which pytest | head",
        "timeout -s KILL 60 git log | tail",
        "env -u FOO python script.py | tail",
        "uv run --with pkg python script.py | tail",
        "coverage report | tail",
        "coverage run script.py | tail",
    )

    def test_each_shape_names_its_sink(self):
        for command, sink in self.DENIED_SHAPES:
            with self.subTest(command=command):
                self.assertEqual(sink, pretool_gate.piped_test_suite_sink(command))

    def test_each_shape_is_denied_by_every_profile_with_the_capture_advice(self):
        for command, sink in self.DENIED_SHAPES:
            for who in PROFILES:
                with self.subTest(command=command, who=who), with_flag():
                    code, msg = profile_bash(who, command)
                    self.assertEqual(DENIED, code, msg)
                    self.assertIn(f"into '{sink}'", msg)
                    self.assertIn("out=$(cmd 2>&1)", msg)

    def test_ordinary_commands_in_the_same_shapes_stay_allowed(self):
        for command in self.ALLOWED_SHAPES:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.piped_test_suite_sink(command))

    def test_with_the_flag_off_the_nested_shapes_pass(self):
        for command, _ in self.DENIED_SHAPES:
            with self.subTest(command=command), with_flag(False):
                self.assertEqual(ALLOWED, pretool_gate.check_piped_test_suite(command))

    def test_a_body_at_the_depth_cap_is_still_read(self):
        command = nest("pytest | tail", pretool_gate.MAX_SHELL_DEPTH)
        self.assertEqual("tail", pretool_gate.piped_test_suite_sink(command))

    def test_a_body_past_the_depth_cap_goes_unread_like_unparseable_text(self):
        command = nest("pytest | tail", pretool_gate.MAX_SHELL_DEPTH + 1)
        self.assertEqual("", pretool_gate.piped_test_suite_sink(command))
        self.assertEqual("", pretool_gate.piped_test_suite_sink("pytest | tail 'unbalanced"))


if __name__ == "__main__":
    unittest.main()

"""Spelling evasions of the three pipeline-script gates (task-0100).

`approve.py` (pretool_gate) and `approvals.py` / `mode.py` (agent_gate, task-0097)
are found by the same helpers - exec_names_of, argv0_index, executed_names,
segments_with_separators, pad_separators - and the same script regexes. The
task-0097 reviewer found spellings of the plain command that none of them saw:
an interpreter flag with a value, a glued `-m`, a wrapper's own arguments, a
trailing dot, a redirect before the script, shell grouping, a multi-hop pipe and
`|&`. Each was ALLOWED for a readonly agent on the unchanged code.

These tests pin the repair once per spelling for all three scripts, in every
profile, and pin that reading (`--show`, no argument) and merely naming a script
stay allowed.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
import tmproot  # noqa: F401  (arms the in-project temp redirect)

PROFILES = ("handle_dev", "handle_readonly", "handle_docs")
PIPE = ".claude/tools/pipeline"

# script file name -> (module name, the phrase every profile's deny carries)
SCRIPTS = {
    "approve.py": ("approve", "approve.py is orchestrator-only"),
    "approvals.py": ("approvals", "approvals.py is orchestrator-only"),
    "mode.py": ("mode", "mode.py is orchestrator-only"),
}

# {S} is the script path, {M} its module name, {A} an argument that SETS and
# @PY@ the plain command, for the templates that wrap it in shell syntax.
INTERPRETER_FLAGS = (
    "python -X utf8 {S} {A}",
    "python -W ignore {S} {A}",
    "python -X utf8 -W ignore {S} {A}",
    "python -u -X utf8 {S} {A}",
    "py -3 -X utf8 {S} {A}",
    "python3 -X utf8 -u {S} {A}",
    "python -Xutf8 {S} {A}",
    "python -Wignore {S} {A}",
    "python -u -Xutf8 -Wignore {S} {A}",
    "python --check-hash-based-pycs always {S} {A}",
    "python --check-hash-based-pycs=always {S} {A}",
    "python -X utf8 --check-hash-based-pycs never {S} {A}",
)
MODULE_FORMS = (
    "cd " + PIPE + " && python -m{M} {A}",
    "cd " + PIPE + " && python -m {M} {A}",
    "cd " + PIPE + " && python -Bm {M} {A}",
    "cd " + PIPE + " && python -Bm{M} {A}",
    "cd " + PIPE + " && python -X utf8 -m{M} {A}",
)
WRAPPERS = (
    "timeout 30 python {S} {A}",
    "timeout 30s python {S} {A}",
    "timeout -k 5 30 python {S} {A}",
    "timeout --signal KILL 30 python {S} {A}",
    "timeout 30 {S} {A}",
    "uv run python {S} {A}",
    "uv run {S} {A}",
    "uv run --with requests python {S} {A}",
    "uv run --python 3.12 python {S} {A}",
    "cmd /c python {S} {A}",
    "cmd.exe /c python {S} {A}",
    "cmd //c python {S} {A}",
    'cmd /c "python {S} {A}"',
    "cmd /c {S} {A}",
    "xargs -n 1 python {S} {A}",
    "env -u FOO python {S} {A}",
    "sudo -u root python {S} {A}",
    "timeout 30 uv run python {S} {A}",
    "nice python {S} {A}",
    "nice -n 10 python {S} {A}",
    "nice -n 10 {S} {A}",
    "stdbuf -oL python {S} {A}",
    "stdbuf -o L python {S} {A}",
    "setsid python {S} {A}",
    "setsid -w python {S} {A}",
    "uvx python {S} {A}",
    "pipenv run python {S} {A}",
    "pipenv run {S} {A}",
)
TRAILING_DOT = (
    "python {S}. {A}",
    "python {S}... {A}",
    'python "{S} " {A}',
    "python -X utf8 {S}. {A}",
)
# A redirect before or between the interpreter and the script hides the script.
REDIRECTS = (
    "python 2>&1 {S} {A}",
    "python >tmp/x {S} {A}",
    "python > tmp/x {S} {A}",
    "python >>tmp/x {S} {A}",
    "python 2>tmp/x {S} {A}",
    "python &>tmp/x {S} {A}",
    "python >&tmp/x {S} {A}",
    "python <tmp/x {S} {A}",
    "python < tmp/x {S} {A}",
    "2>&1 python {S} {A}",
    ">tmp/x python {S} {A}",
    "> tmp/x python {S} {A}",
    "<tmp/x python {S} {A}",
    "< tmp/x python {S} {A}",
    "python -X utf8 2>&1 {S} {A}",
    "env 2>&1 python {S} {A}",
    "python &> tmp/x {S} {A}",
    "python >& tmp/x {S} {A}",
    "python <<< x {S} {A}",
    "python <<<x {S} {A}",
    "<<< x python {S} {A}",
)
GROUPING = (
    "(@PY@)",
    "( @PY@ )",
    "{ @PY@; }",
    "if true; then @PY@; fi",
    "if @PY@; then echo done; fi",
    "for i in 1; do @PY@; done",
    "while true; do @PY@; done",
    "echo x && (@PY@)",
    "cd " + PIPE + " && (@PY@)",
    "! @PY@",
    "if false; then echo no; elif true; then @PY@; else echo no; fi",
    "if false; then echo no; elif @PY@; then echo x; fi",
    "if false; then echo no; else @PY@; fi",
    "while @PY@; do echo x; done",
    "until @PY@; do echo x; done",
    "until false; do @PY@; done",
    # A group's closer must not break the pipe chain that feeds an interpreter.
    "{ cat {S}; } | python - {A}",
    "{ cat {S}; } |& python - {A}",
    "( cat {S} ) | python - {A}",
    "(cat {S}) | python - {A}",
    "( cat {S}; ) | python - {A}",
    "if true; then cat {S}; fi | python - {A}",
    "for i in 1; do cat {S}; done | python - {A}",
    "{ cat {S}; } | cat | python - {A}",
)
PIPES = (
    "cat {S} | cat | python - {A}",
    "cat {S} | cat | cat | python - {A}",
    "cat {S} | head -5 | python - {A}",
    "cat {S} |& python - {A}",
    "cat {S} |& cat |& python - {A}",
    "cat {S} | cat |& python - {A}",
    "cat {S}|&python - {A}",
    "cat {S} | timeout 30 python - {A}",
    "cat {S} | env python - {A}",
    "cat {S} | uv run python - {A}",
    "cat {S} | cat | nice python - {A}",
    "echo {S} | xargs python {A}",
)

# Reading, and merely naming the script, stay allowed (approvals.py / mode.py:
# approve.py has no read form). Every one of these must also stay allowed with the
# new wrapper, flag, redirect and grouping spellings. A name behind a file write
# (`grep x {S} > tmp/f`) is judged by the write rule and tested on its own below.
READS = (
    "python {S}",
    "python {S} --show",
    "python {S} show",
    "python -X utf8 {S} --show",
    "python -W ignore {S}",
    "timeout 30 python {S} --show",
    "uv run python {S} --show",
    "cmd /c python {S} --show",
    "python 2>&1 {S} --show",
    "python {S}. --show",
    "(python {S} --show)",
    "( python {S} )",
    "{ python {S} --show; }",
    "if true; then python {S} --show; fi",
    "for i in 1; do python {S}; done",
    "cd " + PIPE + " && python -m{M} --show",
    "cd " + PIPE + " && python -Bm {M}",
    "python {S} --show |& cat",
    "python {S} --show | cat | cat",
    "python {S} --show | cat | python -c 'import sys'",
    "python {S} --show |& python -m json.tool",
    "python --check-hash-based-pycs always {S} --show",
    "python -Xutf8 -Wignore {S} --show",
    "nice python {S} --show",
    "pipenv run python {S} --show",
    "python <<< x {S} --show",
    "{ python {S} --show; } | cat",
)
NAMED_NOT_RUN = (
    "git show HEAD -- {S}",
    "timeout 30 git show HEAD -- {S}",
    "sudo -u root git show HEAD -- {S}",
    "xargs -n 1 echo {S}",
    "uv run git log -p -- {S}",
    "cmd /c type {S}",
    "(git show HEAD -- {S})",
    "{ cat {S}; }",
    "if true; then cat {S}; fi",
    "cat {S} | cat",
    "cat {S} |& cat",
    "cat {S} | cat | head -5",
    "cat {S} 2>&1",
    "nice git show HEAD -- {S}",
    "stdbuf -oL cat {S}",
    "pipenv run git log -p -- {S}",
    "{ cat {S}; } | cat",
    "( cat {S} ) | cat",
    "if true; then cat {S}; fi | cat",
    "for i in 1; do cat {S}; done | head -5",
)

SETTING_ARG = {"approve.py": "auto", "approvals.py": "auto", "mode.py": "talk"}


def render(template: str, script: str) -> str:
    return (template.replace("@PY@", "python {S} {A}").replace("{S}", f"{PIPE}/{script}")
            .replace("{M}", SCRIPTS[script][0]).replace("{A}", SETTING_ARG[script]))


def run_profile(name: str, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = getattr(agent_gate, name)("Bash", {"command": command}, cwd=".")
    return code, err.getvalue()


class EvasiveSpellingsAreDeniedTest(unittest.TestCase):

    def _assert_denied(self, templates: tuple):
        for script, (_, phrase) in SCRIPTS.items():
            for template in templates:
                command = render(template, script)
                for name in PROFILES:
                    with self.subTest(script=script, profile=name, command=command):
                        code, err = run_profile(name, command)
                        self.assertEqual(2, code, err)
                        self.assertIn(phrase, err)

    def test_an_interpreter_flag_that_takes_a_value_does_not_hide_the_script(self):
        self._assert_denied(INTERPRETER_FLAGS)

    def test_a_glued_or_clustered_module_flag_names_the_script(self):
        self._assert_denied(MODULE_FORMS)

    def test_a_wrapper_with_its_own_arguments_does_not_hide_the_script(self):
        self._assert_denied(WRAPPERS)

    def test_a_trailing_dot_or_space_on_the_script_name_does_not_hide_it(self):
        self._assert_denied(TRAILING_DOT)

    def test_a_redirect_before_or_between_interpreter_and_script_does_not_hide_it(self):
        self._assert_denied(REDIRECTS)

    def test_shell_grouping_and_control_syntax_does_not_hide_the_command(self):
        self._assert_denied(GROUPING)

    def test_a_multi_hop_or_stderr_pipe_into_an_interpreter_runs_the_piped_script(self):
        self._assert_denied(PIPES)


class ReadsAndNamesStayAllowedTest(unittest.TestCase):

    def test_reading_the_level_and_the_mode_stays_allowed_in_every_spelling(self):
        for script in ("approvals.py", "mode.py"):
            for template in READS:
                command = render(template, script)
                for name in PROFILES:
                    with self.subTest(script=script, profile=name, command=command):
                        # Readonly and docs also deny a file write, so the one
                        # redirect-to-a-file read (`> tmp/..`) is not in this list.
                        code, err = run_profile(name, command)
                        self.assertEqual(0, code, err)

    def test_naming_a_script_without_running_it_stays_allowed(self):
        for script in SCRIPTS:
            for template in NAMED_NOT_RUN:
                command = render(template, script)
                for name in PROFILES:
                    with self.subTest(script=script, profile=name, command=command):
                        code, err = run_profile(name, command)
                        self.assertEqual(0, code, err)

    def test_a_named_script_behind_a_file_redirect_is_not_run_by_the_script_checks(self):
        for script in SCRIPTS:
            command = render("grep -n level {S} > tmp/lines.txt", script)
            with self.subTest(script=script):
                self.assertFalse(pretool_gate.runs_approve_script(command))
                self.assertEqual("", agent_gate.sets_pipeline_state(command))


class SharedHelpersTest(unittest.TestCase):
    """The helpers the three gates share, asserted on their own output so a
    regression names the helper rather than a profile."""

    def test_a_stderr_pipe_is_a_pipe_not_a_pipe_followed_by_a_background_marker(self):
        self.assertEqual(
            [("", ["cat", "x"]), ("|&", ["python", "-"])],
            pretool_gate.segments_with_separators("cat x |& python -"))
        self.assertEqual(
            [("", ["cat", "x"]), ("|&", ["python", "-"])],
            pretool_gate.segments_with_separators("cat x|&python -"))

    def test_an_ampersand_that_belongs_to_a_redirect_is_not_a_separator(self):
        for command in ("x 2>&1 y", "x &>f y", "x >&f y", "x <&3 y", "x &>>f y"):
            with self.subTest(command=command):
                self.assertEqual(1, len(pretool_gate.segments_with_separators(command)))

    def test_an_ampersand_redirect_and_its_target_are_not_arguments_of_the_script(self):
        # Without the `&` forms in the token regex, `approvals.py >& tmp/f` read
        # `tmp/f` as a setting argument (the AMP_REDIRECT_RE this replaced did it
        # by deleting the `&` from the command text first).
        for tokens, want in ((["--show", ">&", "tmp/f"], ["--show"]),
                             ([">&", "tmp/f"], []),
                             (["&>", "tmp/f", "--show"], ["--show"]),
                             (["&>>", "tmp/f"], []),
                             (["--show", "2>&1"], ["--show"]),
                             (["2>&1", "--show"], ["--show"])):
            with self.subTest(tokens=tokens):
                self.assertEqual(want, agent_gate.real_arguments(tokens))

    def test_an_ampersand_before_a_less_than_still_ends_the_command(self):
        self.assertEqual(2, len(pretool_gate.segments_with_separators("x &<f y")))

    def test_grouping_syntax_is_not_the_command_that_runs(self):
        for command, want in (("(python s.py a)", ["python", "s.py"]),
                              ("{ python s.py a; }", ["python", "s.py"]),
                              ("if true; then python s.py a; fi", ["python", "s.py"]),
                              ("for i in 1; do python s.py a; done", ["python", "s.py"]),
                              ("! python s.py a", ["python", "s.py"])):
            with self.subTest(command=command):
                names = pretool_gate.executed_names(command)
                self.assertEqual(want, [n for n in names if n in want])

    def test_a_wrappers_own_operands_are_not_the_command_that_runs(self):
        for command, want in (("timeout 30 python s.py a", ["python", "s.py"]),
                              ("timeout -k 5 30 python s.py a", ["python", "s.py"]),
                              ("uv run python s.py a", ["python", "s.py"]),
                              ("uv run --with x python s.py a", ["python", "s.py"]),
                              ("cmd /c python s.py a", ["python", "s.py"]),
                              ("xargs -n 1 python s.py a", ["python", "s.py"])):
            with self.subTest(command=command):
                # A wrapper's own option value may stand in front (`--with x`); the
                # interpreter and its script must still be named behind it.
                self.assertEqual(want, pretool_gate.exec_names_of(command.split())[-2:])

    def test_a_python_cluster_ending_in_m_names_the_module(self):
        for tokens in (["python", "-mmode"], ["python", "-Bm", "mode"], ["python", "-Bmmode"]):
            with self.subTest(tokens=tokens):
                self.assertIn("mode.py", pretool_gate.exec_names_of(tokens))

    def test_a_flag_value_is_not_taken_for_the_script(self):
        self.assertEqual(["python", "s.py"],
                         pretool_gate.exec_names_of(["python", "-X", "utf8", "s.py"]))

    def test_the_script_regexes_tolerate_the_trailing_dots_windows_drops(self):
        for name in ("approve.py", "approve.py.", "x/approve.py...", "x\\approve.py. "):
            with self.subTest(name=name):
                self.assertTrue(pretool_gate.APPROVE_SCRIPT_RE.search(name))
        for name in ("approvals.py", "approvals.py.", "x/mode.py.."):
            with self.subTest(name=name):
                self.assertTrue(agent_gate.STATE_SETTER_RE.search(name))
        self.assertFalse(pretool_gate.APPROVE_SCRIPT_RE.search("approve.pyc"))
        self.assertFalse(agent_gate.STATE_SETTER_RE.search("mode.py.bak"))

    def test_the_script_regexes_tolerate_a_closing_parenthesis_behind_the_name(self):
        for name in ("approve.py)", "x/approve.py )", "x/approve.py.)"):
            with self.subTest(regex="approve", name=name):
                self.assertTrue(pretool_gate.APPROVE_SCRIPT_RE.search(name))
        for name in ("approvals.py)", "mode.py )", "x/mode.py.)"):
            with self.subTest(regex="setter", name=name):
                self.assertTrue(agent_gate.STATE_SETTER_RE.search(name))
        for name in ("handoff.py)", "x/handoff.py )", "x/handoff.py.", "x/handoff.py"):
            with self.subTest(regex="handoff", name=name):
                self.assertTrue(agent_gate.HANDOFF_SCRIPT_RE.search(name))
        self.assertFalse(agent_gate.HANDOFF_SCRIPT_RE.search("handoff.pyc"))

    def test_a_stderr_pipe_into_an_interpreter_runs_the_piped_operand(self):
        self.assertIn("x/approve.py", pretool_gate.executed_names("cat x/approve.py |& python -"))

    def test_a_lone_group_closer_is_no_command_and_keeps_the_pipe_chain_whole(self):
        for closer in ("}", ")", "fi", "done"):
            with self.subTest(closer=closer):
                self.assertEqual([], pretool_gate.ungroup([closer]))
        pairs = pretool_gate.segments_with_separators("{ cat x/approve.py; } | python -")
        self.assertEqual([("", ["cat", "x/approve.py"]), ("|", ["python", "-"])], pairs)
        self.assertIn("x/approve.py", pretool_gate.executed_names("{ cat x/approve.py; } | python -"))

    def test_a_closer_word_with_company_is_still_a_command(self):
        self.assertEqual(["done", "x"], pretool_gate.ungroup(["done", "x"]))
        self.assertEqual(["cat", "x", ")"], pretool_gate.ungroup(["(", "cat", "x", ")"]))

    def test_a_python_option_that_takes_a_value_does_not_hand_it_over_as_the_script(self):
        for tokens in (["python", "--check-hash-based-pycs", "always", "s.py"],
                       ["python", "-X", "utf8", "--check-hash-based-pycs", "never", "s.py"]):
            with self.subTest(tokens=tokens):
                self.assertEqual(["python", "s.py"], pretool_gate.exec_names_of(tokens))
        self.assertEqual(["python", "s.py"],
                         pretool_gate.exec_names_of(["python", "--check-hash-based-pycs=always", "s.py"]))

    def test_nice_stdbuf_setsid_uvx_and_pipenv_run_are_wrappers_of_the_command_behind_them(self):
        for command in ("nice python s.py a", "nice -n 10 python s.py a", "stdbuf -oL python s.py a",
                        "setsid python s.py a", "setsid -w python s.py a", "uvx python s.py a",
                        "pipenv run python s.py a"):
            with self.subTest(command=command):
                self.assertEqual(["python", "s.py"], pretool_gate.exec_names_of(command.split())[-2:])
        self.assertEqual(["s.py"], pretool_gate.exec_names_of("nice -n 10 s.py a".split()))
        self.assertEqual(["git"], pretool_gate.exec_names_of("nice git show s.py".split()))

    def test_every_reserved_word_that_opens_a_command_is_dropped(self):
        for word in ("elif", "else", "while", "until"):
            with self.subTest(word=word):
                self.assertEqual(["python", "s.py"],
                                 pretool_gate.exec_names_of(pretool_gate.ungroup([word, "python", "s.py"])))

    def test_a_herestring_operator_takes_its_word_and_is_not_the_script(self):
        for tokens in (["python", "<<<", "x", "s.py"], ["python", "<<<x", "s.py"],
                       ["<<<", "x", "python", "s.py"]):
            with self.subTest(tokens=tokens):
                self.assertEqual(["python", "s.py"], pretool_gate.exec_names_of(tokens))


class DevBashCannotWriteStateTest(unittest.TestCase):
    """task-0097 closed Write and Edit on .agentry/state/ for the dev profile;
    the same files were still writable through a Bash redirect or a mutating
    command. Reading the state stays open."""

    WRITES = (
        "echo auto > .agentry/state/approvals",
        "echo auto >.agentry/state/approvals",
        "echo auto >> .agentry/state/mode",
        "echo auto 2> .agentry/state/mode",
        "echo auto | tee .agentry/state/approvals",
        "tee -a .agentry/state/mode < tmp/x",
        "sed -i s/manual/auto/ .agentry/state/approvals",
        "sed -i.bak s/a/b/ .agentry/state/mode",
        "cp tmp/x .agentry/state/approvals",
        "mv tmp/x .agentry/state/mode",
        "rm .agentry/state/approvals",
        "truncate -s 0 .agentry/state/mode",
        r"echo auto > .agentry\state\approvals",
        "echo auto > ./.agentry/state/approvals",
        "echo auto > E:/Personal/Agenty/.agentry/state/approvals",
        "echo auto > .agentry/state./approvals",
        "bash -c 'echo auto > .agentry/state/approvals'",
        "(echo auto > .agentry/state/approvals)",
        "echo auto>.agentry/state/approvals",
        'echo auto > ".agentry/state/approvals"',
        "echo auto > '.agentry/state/approvals'",
        "cp -t .agentry/state tmp/x",
        "cp --target-directory=.agentry/state tmp/x",
        "cp --target-directory .agentry/state tmp/x",
        "cp -rt .agentry/state tmp/x",
        "cp -t.agentry/state tmp/x",
        "cp -S .bak -t .agentry/state tmp/x",
        "cp -Stilde tmp/x .agentry/state/approvals",
        "bash -c 'rm .agentry/state/approvals'",
        "sh -c 'tee .agentry/state/mode < tmp/x'",
        "echo $(rm .agentry/state/approvals)",
    )
    ALLOWED = (
        "cat .agentry/state/approvals",
        "ls .agentry/state",
        "grep -n auto .agentry/state/approvals",
        "cp .agentry/state/approvals tmp/approvals.copy",
        "echo auto > tmp/approvals",
        "echo auto > .agentry/tasks/active/task-0001.md",
        "sed -i s/a/b/ .agentry/tasks/active/task-0001.md",
        "tee tmp/out.txt < .agentry/state/mode",
        "git show HEAD -- .agentry/state/approvals",
        "echo state > docs/agentry-state.md",
        "cp -t tmp .agentry/state/approvals",
        "cp --target-directory=tmp .agentry/state/approvals .agentry/state/mode",
        "cp -Sbak .agentry/state/approvals tmp/approvals.copy",
        "bash -c 'cat .agentry/state/approvals'",
    )

    def test_a_dev_redirect_or_mutating_command_on_the_state_tree_is_denied(self):
        for command in self.WRITES:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(2, code, err)
                self.assertIn("orchestrator", err)

    def test_reading_and_writing_elsewhere_stays_allowed_for_dev(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(0, code, err)

    def test_writes_protected_state_names_the_path(self):
        self.assertEqual(".agentry/state/approvals",
                         agent_gate.writes_protected_state("echo auto > .agentry/state/approvals"))
        self.assertEqual("", agent_gate.writes_protected_state("echo auto > tmp/approvals"))


class PipedSuiteStaysDeniedTest(unittest.TestCase):
    """quality-standard.md forbids piping the suite into a filter. Redirects and
    `|&` used to hide the pipe from that rule; these pin that they no longer do."""

    SUITES = (
        "python -m unittest discover 2>&1 | tail",
        "python -m unittest discover -s .claude/tools 2>&1 | tail -5",
        "pytest 2>&1 | head",
        "pytest |& tail",
        "python -m unittest discover |& tail",
        "python -m pytest 2>&1 | grep FAILED",
    )

    def test_a_suite_piped_into_a_filter_is_denied_in_every_profile(self):
        for command in self.SUITES:
            for name in PROFILES:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(2, code, err)

    def test_the_piped_suite_rule_itself_names_the_sink(self):
        for command, sink in (("python -m unittest discover 2>&1 | tail", "tail"),
                              ("pytest 2>&1 | head", "head"),
                              ("pytest |& tail", "tail")):
            with self.subTest(command=command):
                self.assertEqual(sink, pretool_gate.piped_test_suite_sink(command))

    def test_a_captured_or_unpiped_suite_is_not_the_piped_rule(self):
        for command in ("python -m unittest discover 2>&1", "pytest |& cat", "git log | head"):
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.piped_test_suite_sink(command))


if __name__ == "__main__":
    unittest.main()

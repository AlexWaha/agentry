"""A bare NUL is a real file on this host, not a discard sink (task-0074, and
the absorbed task-0076).

Measured 2026-09-14 under git-bash MINGW64 and re-measured for this task: bash
has no device named NUL, so a redirect to a bare `NUL` opens a REGULAR FILE in
the current directory. Win32 then resolves that name as the reserved device,
which is why Python reports `is_file()` False for it, `exists()` True for it in
every directory whether or not the entry is there, and cannot unlink it
(WinError 5). Only bash (`rm -f`) removes it.

Two halves, one artifact:

* The gates (`redirect_write_target`, reached by agent_gate's readonly and docs
  profiles and by the orchestrator gate) used to list `nul` as a discard sink,
  so an agent that could not write could create the one file nothing can
  delete. Pure string tests, run on every platform.
* `session_start.cleanup_nul()` could not remove the entry it exists to remove.
  These tests need a REAL entry, so they run only on Windows with Git bash, and
  they build it the way task-0070 did: `touch` through the pinned Git bash, no
  shell redirect in any spelling. A bare `bash` from Python on this host is WSL
  bash, a different operating system, so the binary is pinned and `uname` is
  asserted before any of it is believed.

Entries are removed with bash `rm -f` in a cleanup registered AFTER the
sandbox's own, so it runs first: shutil.rmtree would fail on the entry.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate  # noqa: E402 - path set above
import pretool_gate  # noqa: E402
import session_start  # noqa: E402
import tmproot  # noqa: E402

GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
CLEANUP_SCRIPT = TOOLS_DIR.parent / "hooks" / "cleanup-nul.sh"
HAVE_GIT_BASH = os.name == "nt" and Path(GIT_BASH).is_file()
needs_git_bash = unittest.skipUnless(HAVE_GIT_BASH, "needs Windows with Git bash")


def bash(command: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([GIT_BASH, "-c", command, "_", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=30)


def make_nul_entry(case: unittest.TestCase, root: Path, name: str = "NUL") -> None:
    """A real directory entry named `name`, made without a shell redirect."""
    uname = bash("uname -s", cwd=root).stdout
    case.assertRegex(uname, r"MINGW|MSYS", "not Git bash - the fixture would test another OS")
    case.addCleanup(remove_entry, root, name)
    bash('touch -- "$1"', name, cwd=root)
    case.assertIn(name, os.listdir(root), "fixture failed: the entry was not created")


def remove_entry(root: Path, name: str) -> None:
    bash('rm -f -- "$1"', name, cwd=root)
    if name in os.listdir(root):
        raise AssertionError(f"fixture cleanup failed: {name} is still in {root}")


def run_cleanup(root: Path) -> str:
    """cleanup_nul() aimed at `root`, with everything it prints returned."""
    out = io.StringIO()
    with unittest.mock.patch.object(session_start, "ROOT", root), redirect_stdout(out):
        session_start.cleanup_nul()
    return out.getvalue()


def stderr_of(call, *args) -> tuple[int, str]:
    err = io.StringIO()
    with unittest.mock.patch("sys.stderr", err):
        code = call(*args)
    return code, err.getvalue()


CASES = ("NUL", "nul", "Nul")


class BareNulIsARealFileGateTest(unittest.TestCase):
    """Both profiles are driven separately: each one was allowed on its own."""

    def bash_call(self, handler, command: str) -> tuple[int, str]:
        return stderr_of(handler, "Bash", {"command": command})

    def assert_denied_as_a_real_file(self, handler, command: str) -> None:
        code, msg = self.bash_call(handler, command)
        self.assertEqual(2, code, f"a redirect to a bare NUL was ALLOWED: {command!r}")
        self.assertIn("real file", msg)
        self.assertNotIn("discard", msg, "the deny repeats the sink reading")

    def test_a_readonly_agent_is_denied_a_bare_nul_redirect_in_every_case(self):
        for name in CASES:
            for form in (f"ls 2>{name}", f"ls >{name}", f"ls > {name}",
                         f"ls >{name} 2>&1", f"ls &>{name}", f'ls > "{name}"',
                         f"bash -c 'ls 2>{name}'"):
                with self.subTest(command=form):
                    self.assert_denied_as_a_real_file(agent_gate.handle_readonly, form)

    def test_a_docs_agent_is_denied_a_bare_nul_redirect_in_every_case(self):
        for name in CASES:
            for form in (f"ls 2>{name}", f"ls >{name}", f"ls > {name}",
                         f"ls >{name} 2>&1", f"ls &>{name}", f'ls > "{name}"',
                         f"bash -c 'ls 2>{name}'"):
                with self.subTest(command=form):
                    self.assert_denied_as_a_real_file(agent_gate.handle_docs, form)

    def test_the_orchestrator_gate_is_denied_a_bare_nul_redirect_in_every_case(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            for name in CASES:
                with self.subTest(command=f"ls 2>{name}"):
                    code, msg = stderr_of(pretool_gate.orch_check_bash, f"ls 2>{name}")
                    self.assertEqual(2, code, "orchestrator_gate allowed a bare NUL redirect")
                    self.assertIn("real file", msg)
                    self.assertNotIn("discard", msg)

    SUBSTITUTIONS = ("echo $(ls 2>NUL)", "(ls 2>NUL)", "echo `ls 2>NUL`",
                     "echo $(ls 2>nul)", 'echo $(ls 2>"NUL")')

    def test_a_bare_nul_inside_a_command_substitution_carries_the_real_file_note(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            for form in self.SUBSTITUTIONS:
                with self.subTest(command=form):
                    self.assert_denied_as_a_real_file(agent_gate.handle_readonly, form)
                    self.assert_denied_as_a_real_file(agent_gate.handle_docs, form)
                    code, msg = stderr_of(pretool_gate.orch_check_bash, form)
                    self.assertEqual(2, code)
                    self.assertIn("real file", msg)

    def test_a_path_qualified_nul_inside_a_substitution_still_gets_no_note(self):
        code, msg = self.bash_call(agent_gate.handle_readonly, "echo $(ls 2>./NUL)")
        self.assertEqual(2, code)
        self.assertNotIn("real file", msg)

    def test_the_control_a_real_target_is_denied_by_the_same_profiles(self):
        # Without this a red result above could mean the harness never denies a
        # redirect at all, which is a different bug from the one claimed.
        for handler in (agent_gate.handle_readonly, agent_gate.handle_docs):
            with self.subTest(handler=handler.__name__):
                code, msg = self.bash_call(handler, "ls > out.txt")
                self.assertEqual(2, code)
                self.assertNotIn("real file", msg)

    def test_the_classifier_itself_reports_the_bare_nul_write(self):
        self.assertEqual("2>NUL", pretool_gate.redirect_write_target("ls 2>NUL"))
        self.assertEqual("> nul", pretool_gate.redirect_write_target("ls > nul"))

    def test_the_nested_body_of_a_shell_is_still_unwrapped(self):
        self.assertEqual("> Nul", pretool_gate.redirect_write_target('bash -c "ls > Nul"'))


class PathQualifiedNulStaysDeniedTest(unittest.TestCase):
    """Already caught before this task, and it must stay caught: a fix that
    REPLACED the working comparison (basename, endswith, a new regex) instead of
    adding to it would turn each of these into an allow. Green on unchanged
    code by design - the value is in what it refuses to let the fix trade away
    (lesson a-mechanical-rename-finds-strings-not-meanings)."""

    TARGETS = ("./NUL", "sub/NUL", "sub/nul", "../NUL", "E:/proj/tmp/NUL", "C:\\x\\Nul")

    def test_every_path_qualified_form_is_still_a_write(self):
        for target in self.TARGETS:
            with self.subTest(target=target):
                self.assertEqual(f"2>{target}",
                                 pretool_gate.redirect_write_target(f"ls 2>{target}"))

    def test_both_profiles_still_deny_them(self):
        for handler in (agent_gate.handle_readonly, agent_gate.handle_docs):
            for target in self.TARGETS:
                with self.subTest(handler=handler.__name__, target=target):
                    code, _ = stderr_of(handler, "Bash", {"command": f"ls 2>{target}"})
                    self.assertEqual(2, code)

    def test_the_two_other_sinks_are_untouched(self):
        # Descriptor dups and the Unix device stay non-writes; only the bare NUL
        # changed classification.
        unix_null = "/" + "dev" + "/" + "null"
        self.assertEqual("", pretool_gate.redirect_write_target("make 2>&1"))
        self.assertEqual("", pretool_gate.redirect_write_target(f"make 2>{unix_null}"))


class NamesContainingNulAreNotMatchedTest(unittest.TestCase):
    """No substring matching: a legitimate write to `nullable.py` or `annul.txt`
    is judged exactly as before."""

    NAMES = ("nullable.py", "annul.txt", "nul.txt", "NULL")

    def test_a_dev_agent_may_still_write_them(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                code, _ = stderr_of(agent_gate.handle_dev, "Bash",
                                    {"command": f"echo x > {name}"})
                self.assertEqual(0, code)

    def test_the_orchestrator_may_still_write_them_under_docs(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            for name in self.NAMES:
                with self.subTest(name=name):
                    code, _ = stderr_of(pretool_gate.orch_check_bash,
                                        f"echo x > docs/{name}")
                    self.assertEqual(0, code)

    def test_a_docs_agent_may_still_write_them_with_the_write_tool(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                code, _ = stderr_of(agent_gate.handle_docs, "Write",
                                    {"file_path": f"docs/{name}", "content": "x"})
                self.assertEqual(0, code)

    def test_the_real_file_note_is_not_attached_to_them(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                _, msg = stderr_of(agent_gate.handle_readonly, "Bash",
                                   {"command": f"echo x > {name}"})
                self.assertIn(f"> {name}", msg)
                self.assertNotIn("real file", msg)


@needs_git_bash
class ExistsIsATrapTest(unittest.TestCase):
    """Why the fix lists the directory instead of asking about the path."""

    def test_exists_is_true_for_the_name_in_a_directory_with_no_such_entry(self):
        root = tmproot.sandbox(self, "nulexists")
        self.assertEqual([], os.listdir(root))
        self.assertTrue((root / "NUL").exists(), "the device name resolves everywhere")
        self.assertFalse((root / "NUL").is_file())

    def test_the_directory_listing_tells_the_two_cases_apart(self):
        empty = tmproot.sandbox(self, "nulempty")
        full = tmproot.sandbox(self, "nulfull")
        make_nul_entry(self, full)
        self.assertEqual([], session_start.stray_nul_entries(empty))
        self.assertEqual(["NUL"], session_start.stray_nul_entries(full))


@needs_git_bash
class CleanupNulTest(unittest.TestCase):

    def test_a_real_entry_is_actually_gone_afterwards_in_every_case(self):
        for name in CASES:
            with self.subTest(name=name):
                root = tmproot.sandbox(self, "nulclean")
                make_nul_entry(self, root, name)
                printed = run_cleanup(root)
                # The parent listing, not the path: exists() lies about this name.
                self.assertEqual([], os.listdir(root), "the entry is still in the directory")
                self.assertEqual("", printed, "a clean success should be quiet")

    def test_no_unlink_is_attempted_where_there_is_no_entry(self):
        root = tmproot.sandbox(self, "nulnone")
        with unittest.mock.patch.object(session_start.subprocess, "run") as run, \
                unittest.mock.patch("os.unlink") as unlink, \
                unittest.mock.patch("os.remove") as remove, \
                unittest.mock.patch.object(Path, "unlink") as path_unlink:
            printed = run_cleanup(root)
        run.assert_not_called()
        unlink.assert_not_called()
        remove.assert_not_called()
        path_unlink.assert_not_called()
        self.assertEqual("", printed)

    def test_other_entries_are_left_alone(self):
        root = tmproot.sandbox(self, "nulkeep")
        (root / "nullable.py").write_text("x", encoding="utf-8")
        make_nul_entry(self, root)
        run_cleanup(root)
        self.assertEqual(["nullable.py"], os.listdir(root))

    def test_the_python_path_and_the_shell_script_agree(self):
        by_python = tmproot.sandbox(self, "nulpy")
        by_script = tmproot.sandbox(self, "nulsh")
        make_nul_entry(self, by_python)
        make_nul_entry(self, by_script)
        run_cleanup(by_python)
        proc = subprocess.run([GIT_BASH, str(CLEANUP_SCRIPT)], cwd=by_script,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(os.listdir(by_script), os.listdir(by_python))
        self.assertEqual([], os.listdir(by_python))

    def test_a_failed_removal_is_reported_not_swallowed(self):
        root = tmproot.sandbox(self, "nulfail")
        make_nul_entry(self, root)
        failed = subprocess.CompletedProcess([], 1, "", "rm: cannot remove 'NUL': denied")
        with unittest.mock.patch.object(session_start.subprocess, "run", return_value=failed):
            printed = run_cleanup(root)
        self.assertIn("NUL", printed)
        self.assertIn("denied", printed)

    def test_a_bash_that_cannot_be_started_is_reported(self):
        root = tmproot.sandbox(self, "nulspawn")
        make_nul_entry(self, root)
        with unittest.mock.patch.object(session_start.subprocess, "run",
                                        side_effect=OSError("no such file")):
            printed = run_cleanup(root)
        self.assertIn("no such file", printed)

    def test_an_unreadable_directory_is_reported(self):
        gone = tmproot.sandbox(self, "nulgone") / "missing"
        printed = run_cleanup(gone)
        self.assertIn("nul cleanup", printed)
        self.assertIn("missing", printed)


@needs_git_bash
class OnlyRealFilesAreTargetedTest(unittest.TestCase):

    def test_a_directory_named_nul_is_not_listed_and_no_bash_is_run(self):
        root = tmproot.sandbox(self, "nuldir")
        self.addCleanup(lambda: bash('rm -rf -- "$1"', "nul", cwd=root))
        bash('mkdir -- "$1"', "nul", cwd=root)
        self.assertIn("nul", os.listdir(root), "fixture failed: the directory was not created")
        self.assertEqual([], session_start.stray_nul_entries(root))
        with unittest.mock.patch.object(session_start.subprocess, "run") as run:
            printed = run_cleanup(root)
        run.assert_not_called()
        self.assertEqual("", printed)


class CleanupNulNeverBlocksTheRestOfTheHookTest(unittest.TestCase):
    """Platform-neutral: the entry list and bash are faked, so the only thing
    under test is that no failure in step 1 stops steps 2 to 3.5."""

    STEPS = ("pipeline_mode", "pipeline_resume", "codegraph_sync", "codebase_memory_check")

    def run_main(self, run_side_effect=None, find_bash_side_effect=None, run_kwargs=None):
        run = unittest.mock.Mock(side_effect=run_side_effect,
                                 return_value=subprocess.CompletedProcess([], 0, "", ""))
        steps = {name: unittest.mock.Mock() for name in self.STEPS}
        out = io.StringIO()
        with unittest.mock.patch.object(session_start, "stray_nul_entries", return_value=["NUL"]), \
                unittest.mock.patch.object(session_start, "find_bash", return_value="bash",
                                           side_effect=find_bash_side_effect), \
                unittest.mock.patch.object(session_start.subprocess, "run", run), \
                unittest.mock.patch.multiple(session_start, **steps), \
                redirect_stdout(out):
            code = session_start.main()
        return code, steps, run, out.getvalue()

    def assert_every_later_step_ran(self, steps):
        for name, step in steps.items():
            self.assertTrue(step.called, f"{name} never ran after the nul cleanup failed")

    def test_a_non_decodable_byte_from_bash_does_not_stop_the_later_steps(self):
        error = UnicodeDecodeError("cp1252", b"\x81", 0, 1, "character maps to <undefined>")
        code, steps, _, printed = self.run_main(run_side_effect=error)
        self.assertEqual(0, code)
        self.assert_every_later_step_ran(steps)
        self.assertIn("nul cleanup", printed)

    def test_bash_lookup_raising_does_not_stop_the_later_steps(self):
        code, steps, _, printed = self.run_main(find_bash_side_effect=RuntimeError("boom"))
        self.assertEqual(0, code)
        self.assert_every_later_step_ran(steps)
        self.assertIn("boom", printed)

    def test_bash_output_is_decoded_as_utf8_with_replacement_and_a_short_timeout(self):
        _, _, run, _ = self.run_main()
        kwargs = run.call_args.kwargs
        self.assertEqual("utf-8", kwargs["encoding"])
        self.assertEqual("replace", kwargs["errors"])
        self.assertEqual(5, kwargs["timeout"])
        self.assertNotIn("text", kwargs, "text=True decodes with the locale codec")


@needs_git_bash
class NoBashTest(unittest.TestCase):
    """The Python path delegates removal to bash. Where there is none it must
    say so and leave the entry, never guess and never crash."""

    def test_with_no_bash_the_entry_stays_and_the_reason_is_printed(self):
        root = tmproot.sandbox(self, "nulnobash")
        make_nul_entry(self, root)
        with unittest.mock.patch.object(session_start, "find_bash", return_value=None), \
                unittest.mock.patch.object(session_start.subprocess, "run") as run:
            printed = run_cleanup(root)
        run.assert_not_called()
        self.assertEqual(["NUL"], os.listdir(root))
        self.assertIn("bash", printed)
        self.assertIn("NUL", printed)

    def test_git_bash_is_found_beside_git_not_by_a_bare_bash_lookup(self):
        # A PATH that offers WSL bash first is the trap: it must not be chosen.
        real_which = session_start.shutil.which
        wsl = "C:/Windows/System32/bash.exe"
        with unittest.mock.patch.object(
                session_start.shutil, "which",
                side_effect=lambda name: wsl if name == "bash" else real_which(name)):
            found = session_start.find_bash()
        self.assertIsNotNone(found)
        self.assertTrue(Path(found).is_file())
        self.assertEqual(Path(GIT_BASH).resolve(), Path(found).resolve())


if __name__ == "__main__":
    unittest.main()

"""The temp-location policy, asserted rather than trusted (task-0069).

The guard this replaces was not a weak guard, it was no guard: nineteen
`tempfile` calls with no `dir=`, and nothing anywhere that would notice. Twelve
`supervisor*.lock` files and nineteen directories accumulated in the user
profile across sessions while every round reported a clean tree, because every
check this project runs looks inside the project and the artifacts were outside
it. Absence of a report was read as absence of a leak.

So the invariants are tests now, and the strongest of them is the last one: a
full green run leaves the project-local temp directory EMPTY. That is the
assertion that cannot rot quietly, because a single leaked directory fails it.

THE FILENAME IS LOAD-BEARING. `unittest discover` loads modules in sorted
order, so `test_zz_...` runs after every other test file, which is what makes
"when the suite ends" mean anything. Renaming it earlier in the alphabet would
leave the last two tests asserting the state of a half-finished run.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import tmproot  # noqa: E402 - tmproot sits beside this file, not on sys.path

# Any route to the module: attribute access (`tempfile.mkdtemp(`,
# `tempfile.TemporaryDirectory(`), a from-import that hides the module name
# (`from tempfile import mkdtemp`), and an alias that hides it just as well
# (`import tempfile as tf`, which is what the first mutation written for this
# test used to slip past an attribute-only pattern). Prose that merely mentions
# the module does not match.
TEMPFILE_USE = re.compile(r"\btempfile\s*\.|^\s*import\s+tempfile\b"
                          r"|^\s*from\s+tempfile\s+import\b", re.MULTILINE)


class LocationIsDecidedInOnePlaceTest(unittest.TestCase):
    """One decision, not nineteen call sites that each got it right."""

    def test_the_temp_root_is_inside_the_project(self):
        self.assertTrue(tmproot.inside_project(tmproot.TMP_ROOT))
        self.assertEqual(tmproot.PROJECT_ROOT / "tmp", tmproot.TMP_ROOT)

    def test_the_whole_process_writes_its_temp_files_inside_the_project(self):
        # Layer 2 of the policy: a call that forgets `dir=` still cannot escape,
        # and neither can a child process, which is how the supervisor tests
        # spawn real pythons and real gits.
        self.assertEqual(str(tmproot.RUN_ROOT), tempfile.gettempdir())
        self.assertEqual(tmproot.TMP_ROOT, tmproot.RUN_ROOT.parent)
        for var in ("TMPDIR", "TEMP", "TMP"):
            with self.subTest(variable=var):
                self.assertTrue(tmproot.inside_project(os.environ[var]),
                                f"{var}={os.environ.get(var)!r} points outside the project")

    def test_a_forgotten_dir_argument_still_lands_in_the_project(self):
        # The redirect proven by using it, rather than by reading the constant
        # back: this is the exact call shape that leaked, with no dir= at all.
        stray = Path(tempfile.mkdtemp())
        self.addCleanup(tmproot.rmtree, stray)
        self.assertTrue(tmproot.inside_project(stray), f"{stray} escaped the project")

    def test_no_test_file_calls_tempfile_directly(self):
        # The anti-copy rule. The next test cannot get the location wrong by
        # copying an older one, because there is no older one to copy: every
        # test file goes through tmproot, and this file is the only place in
        # .claude/tools/tests that names tempfile besides tmproot.py itself.
        offenders = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            if path.name == Path(__file__).name:
                continue
            if TEMPFILE_USE.search(path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         "these files reach for tempfile directly instead of "
                         "using tmproot.sandbox(): " + ", ".join(offenders))

    def test_this_file_still_sorts_last_in_the_directory(self):
        # The module docstring calls the filename load-bearing, and until this
        # assertion existed that claim was documentation only: a `test_zzz_*.py`
        # landing later in the alphabet would silently move the two end-of-run
        # assertions into the middle of the run, where they cover a subset and
        # still report green. That is the exact rot mode this task exists to
        # close, so it is asserted rather than described.
        # max() over the names is the last one in the sorted order unittest
        # discovery loads them in.
        last = max(p.name for p in TESTS_DIR.glob("test_*.py"))
        self.assertEqual(Path(__file__).name, last,
                         f"{last} sorts after this file, so the end-of-run "
                         f"assertions no longer run at the end")

    def test_the_temp_root_is_gitignored(self):
        # Quoted in the task report as well, but asserted here so the ignore
        # entry cannot be dropped without a red test. An unignored tmp/ would
        # put throwaway git repositories into `git status` and, eventually, into
        # a commit.
        probe = tmproot.TMP_ROOT / "ignored-probe.txt"
        done = subprocess.run(["git", "check-ignore", "-v", str(probe)],
                              cwd=str(tmproot.PROJECT_ROOT), capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(0, done.returncode,
                         f"git does not ignore {probe}: {done.stdout}{done.stderr}")
        self.assertIn("tmp/", done.stdout)


class CleanupIsEnforcedTest(unittest.TestCase):
    """Deleted, not hoped to be deleted."""

    def test_a_sandbox_is_deleted_even_when_the_test_that_owns_it_fails(self):
        # The criterion is "a test that fails or raises must not leave its
        # directory behind", so the proof runs a genuinely failing test rather
        # than a passing one. addCleanup is what makes this hold; a try/finally
        # in each test would be one author's memory away from not holding.
        created = []

        class Failing(unittest.TestCase):
            def runTest(inner):
                created.append(tmproot.sandbox(inner, "policy_failing_"))
                inner.fail("deliberate, to prove cleanup runs anyway")

        result = unittest.TestResult()
        Failing().run(result)
        self.assertEqual(1, len(result.failures), "the probe was supposed to fail")
        self.assertEqual(1, len(created))
        self.assertFalse(created[0].exists(),
                         f"{created[0]} survived a failing test")

    def test_a_sandbox_is_deleted_when_setup_itself_raises(self):
        # tearDown does not run when setUp raises; addCleanup does. The old
        # cleanups were registered the same way, so this pins the behaviour
        # rather than changing it.
        created = []

        class Exploding(unittest.TestCase):
            def setUp(inner):
                created.append(tmproot.sandbox(inner, "policy_setup_"))
                raise RuntimeError("deliberate, halfway through setUp")

            def runTest(inner):
                pass

        result = unittest.TestResult()
        Exploding().run(result)
        self.assertEqual(1, len(result.errors))
        self.assertFalse(created[0].exists(),
                         f"{created[0]} survived a setUp that raised")

    def test_a_failed_delete_raises_instead_of_being_ignored(self):
        # How the six leftover `sup-test-*` directories survived: rmtree ran
        # with ignore_errors=True, the delete failed because a detached
        # grandchild still held an inherited handle, and nothing said a word.
        # Here the same failure is loud. Simulated by a path that cannot be
        # removed because it is a file with a directory's name in the call.
        doomed = tmproot.sandbox(self, "policy_undeletable_")
        with unittest.mock.patch.object(tmproot.shutil, "rmtree",
                                        side_effect=PermissionError("held open")):
            with unittest.mock.patch.object(tmproot, "_DELETE_ATTEMPTS", 2), \
                 unittest.mock.patch.object(tmproot, "_DELETE_PAUSE", 0):
                with self.assertRaises(AssertionError) as ctx:
                    tmproot.rmtree(doomed)
        self.assertIn("could not be deleted", str(ctx.exception))
        self.assertIn("held open", str(ctx.exception))


class NothingEscapedTest(unittest.TestCase):
    """The two assertions about the state of the world once the run is over.
    They only mean something because this module sorts last - see the module
    docstring."""

    def test_nothing_was_created_outside_the_project_tree_during_the_run(self):
        # The criterion in full: not "nothing was LEFT outside the project" but
        # "nothing was CREATED outside it". The difference is not academic - the
        # first mutation written for this test (TempRepo pointed at the host
        # temp) passed the leftovers check below, because all 45 repositories
        # were created there and then deleted again, and a snapshot diff sees
        # only what survives. tmproot's audit hook sees the creation itself.
        #
        # MUTATION PROOF: with TempRepo's mkdtemp pointed at tmproot.HOST_TMP,
        # this fails with 45 escapes listed, the first being
        # `os.mkdir: C:\Users\...\AppData\Local\Temp\gate_repo_<random>`.
        self.assertEqual([], tmproot.ESCAPES[:20],
                         f"{len(tmproot.ESCAPES)} creations outside "
                         f"{tmproot.PROJECT_ROOT}")

    def test_nothing_new_appeared_in_the_host_temp_directory(self):
        # The invariant the task asks for: no artifact created outside the
        # project tree during a full run. Compared against the listing taken
        # when tmproot was first imported, and filtered to names the suite could
        # have produced (every prefix sandbox() handed out, plus the stdlib's
        # bare `tmp` default and the supervisor lock) so an unrelated
        # application writing to the host temp mid-run cannot make this flaky.
        #
        # MUTATION PROOF: point TempRepo's mkdtemp at tmproot.HOST_TMP and this
        # fails with 45 `gate_repo_*` names listed.
        if not tmproot.HOST_TMP.is_dir():
            self.skipTest(f"no host temp directory at {tmproot.HOST_TMP}")
        if tmproot.inside_project(tmproot.HOST_TMP):
            self.skipTest("the host temp directory is already inside the project")
        appeared = set(os.listdir(tmproot.HOST_TMP)) - tmproot.HOST_TMP_AT_START
        ours = sorted(name for name in appeared
                      if name == tmproot.RUN_ROOT.name
                      or any(name.startswith(p) for p in tmproot.PREFIXES))
        self.assertEqual([], ours,
                         f"the suite wrote outside the project, into "
                         f"{tmproot.HOST_TMP}: {ours}")

    def test_the_project_temp_directory_is_empty_when_the_suite_ends(self):
        # The strongest criterion in task-0069, and the reason the rest cannot
        # rot: every sandbox this suite created has to be gone by now. A cleanup
        # that silently gives up, a fixture that forgets addCleanup, or a new
        # test that makes its own directory all show up here as a name.
        #
        # Scoped to this run's directory (tmproot.RUN_ROOT), not to TMP_ROOT, so
        # a second suite running concurrently in the same workspace cannot turn
        # this red - measured, that happened while the fixture was being written.
        # RUN_ROOT itself is removed at interpreter exit, so a green run leaves
        # TMP_ROOT as it found it.
        # A hard assertion, with no "the directory is missing, call it empty"
        # branch. That branch existed only because tmproot used to sweep other
        # processes' run directories at import and could race a live sibling.
        # The sweep is gone, so the escape hatch is gone with it - an escape
        # hatch in the strongest guard this task has was the wrong trade.
        self.assertTrue(tmproot.RUN_ROOT.is_dir(),
                        f"{tmproot.RUN_ROOT} vanished mid-run")
        leftovers = sorted(p.name for p in tmproot.RUN_ROOT.iterdir())
        self.assertEqual([], leftovers,
                         f"{tmproot.RUN_ROOT} is not empty after the run: {leftovers}")

    def test_no_sandbox_from_this_run_sits_beside_the_run_directory(self):
        # The other half of the same criterion: a fixture that reaches past
        # mkdtemp() and writes straight into TMP_ROOT would be invisible to the
        # assertion above. Anything of ours that is not the run directory is a
        # leak; other runs' `run-<pid>` directories are somebody else's business.
        stray = sorted(p.name for p in tmproot.TMP_ROOT.iterdir()
                       if any(p.name.startswith(prefix) for prefix in tmproot.PREFIXES))
        self.assertEqual([], stray,
                         f"sandboxes were created outside {tmproot.RUN_ROOT}: {stray}")

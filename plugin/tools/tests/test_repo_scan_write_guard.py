"""Security fix H2 (umbrella task-0118): `repo_scan.py --write` was an unguarded
writer of the protected pipeline.json. Its `repos{}.stages` command lists are what
stack_gate.py runs with shell=True, so a dev agent that could write them could
run anything at the next gate.

Three layers are pinned here:
  1. agent_gate denies the `--write` spelling to EVERY subagent profile (and
     leaves the read-only forms of the script alone);
  2. repo_scan.write() refuses the live config inside Claude Code unless the
     orchestrator attests the main thread, and never touches any other file's
     rules;
  3. the write is atomic (temp file in the same directory + os.replace), which
     also covers the half-written-file case (L2).

Every config here lives in a sandbox; the real .agentry/pipeline.json is only
named, never opened for writing.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "setup"))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
import repo_scan
import state
import tmproot
from gate_allow import assert_not_denied

PROFILES = ("handle_dev", "handle_readonly", "handle_docs")
SCAN = "plugin/tools/setup/repo_scan.py"
LIVE = ".agentry/pipeline.json"

WRITES = (
    f"python {SCAN} --root . --write {LIVE}",
    f"python {SCAN} --root . --table tmp/repos.json --write {LIVE}",
    f"python {SCAN} --write={LIVE}",
    f"python {SCAN} --root . --write=other.json",
    f"python {SCAN} --w {LIVE}",
    f"python {SCAN} --wr {LIVE}",
    f"python {SCAN} --writ {LIVE}",
    f"python3 {SCAN} --write {LIVE}",
    f"py -3 {SCAN} --write {LIVE}",
    f"python -u {SCAN} --write {LIVE}",
    f"python E:/Personal/Agenty/src/{SCAN} --write {LIVE}",
    f"python 'E:\\Personal\\Agenty\\src\\plugin\\tools\\setup\\repo_scan.py' --write {LIVE}",
    f"python {SCAN} --write {LIVE} --root .",
    f"python -m repo_scan --write {LIVE}",
    f"cd plugin/tools/setup && python repo_scan.py --write ../../../{LIVE}",
    f"cd plugin/tools/setup && python3 -m repo_scan --root ../../.. --write ../../../{LIVE}",
    f"env python {SCAN} --write {LIVE}",
    f"PIPELINE_LANE=x python {SCAN} --write {LIVE}",
    f"echo ok; python {SCAN} --write {LIVE}",
    f"python {SCAN} --root . 2>&1 --write {LIVE}",
    f"bash -c 'python {SCAN} --write {LIVE}'",
    f"sh -c \"bash -c 'python {SCAN} --write {LIVE}'\"",
    f"echo {LIVE} | xargs python {SCAN} --write",
    f"python - --write {LIVE} < {SCAN}",
    f"cat {SCAN} | python - --write {LIVE}",
    f"x=$(python {SCAN} --write {LIVE})",
    "python -c \"import repo_scan; repo_scan.write(p, rows)\"",
    "python -c 'import sys; sys.path.insert(0, \"x\"); from repo_scan import write; write(p, r)'",
)

READS = (
    f"python {SCAN} --root .",
    f"python {SCAN} --root . --json",
    f"python {SCAN} --root . --table tmp/repos.json",
    f"python {SCAN}",
    "python -m unittest tests.test_repo_scan",
    "python -m unittest tests.test_repo_scan.WriteTest.test_x_write",
    f"cat {SCAN}",
    f"git show HEAD:{SCAN}",
    f"grep -n write {SCAN}",
    f"python {SCAN} --root . | python -m json.tool",
    "git log --grep='repo_scan.py --write'",
)


def run_profile(name: str, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = getattr(agent_gate, name)("Bash", {"command": command}, cwd=".")
    return code, err.getvalue()


class AgentGateDeniesTheWriteTest(unittest.TestCase):

    def test_every_profile_denies_every_write_spelling(self):
        for name in PROFILES:
            for command in WRITES:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(2, code, err)
                    self.assertIn("repo_scan.py", err)
                    self.assertIn("orchestrator-only", err)

    def test_the_setter_list_names_repo_scan_next_to_the_other_three(self):
        for script in ("approvals.py", "mode.py", "swarm.py", "repo_scan.py"):
            with self.subTest(script=script):
                self.assertTrue(agent_gate.STATE_SETTER_RE.search(f"x/{script}"))
        self.assertFalse(agent_gate.STATE_SETTER_RE.search("repo_scan.py.bak"))
        self.assertFalse(agent_gate.STATE_SETTER_RE.search("my_repo_scan.py"))

    def test_the_read_only_forms_and_mentions_stay_allowed(self):
        for name in PROFILES:
            for command in READS:
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    assert_not_denied(self, name, code, err)

    def test_the_verdict_function_names_the_script_only_for_writes(self):
        for command in WRITES:
            with self.subTest(command=command):
                self.assertEqual("repo_scan.py", agent_gate.sets_pipeline_state(command))
        for command in READS:
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.sets_pipeline_state(command))

    def test_nesting_past_the_depth_limit_is_refused_not_waved_through(self):
        past = f"python {SCAN} --root ."
        for _ in range(pretool_gate.MAX_SHELL_DEPTH + 1):
            past = f"bash -c {shlex.quote(past)}"
        self.assertTrue(agent_gate.sets_pipeline_state(past))

    def test_the_orchestrator_main_thread_gate_does_not_deny_it(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(f"python {SCAN} --write {LIVE}", ".", orch=True)
        self.assertEqual(0, code, err.getvalue())


ROWS = [{"key": "agentry", "path": ".", "stack": "python"}]


class Harness(unittest.TestCase):
    ROWS = ROWS
    CONFIG = '{\n  "cwd": ".",\n  "repos": {},\n  "tail": 1\n}\n'

    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_guard_").resolve()
        self.live = self.root / "pipeline.json"
        self.live.write_bytes(self.CONFIG.encode("utf-8"))
        self.other = self.root / "other.json"
        self.other.write_bytes(self.CONFIG.encode("utf-8"))
        patcher = unittest.mock.patch.object(state, "PIPELINE_PATH", self.live)
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, **values):
        """The environment the caller runs in: only the named keys of the two the
        guard reads, whatever this test run itself inherited."""
        base = {k: v for k, v in os.environ.items()
                if k not in (repo_scan.CLAUDE_ENV, repo_scan.MAIN_THREAD_ENV)}
        patcher = unittest.mock.patch.dict(os.environ, {**base, **values}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bytes_of(self, path: Path) -> bytes:
        return path.read_bytes()

    def leftovers(self) -> list:
        return sorted(p.name for p in self.root.iterdir()
                      if p.name not in ("pipeline.json", "other.json"))


class LiveConfigGuardTest(Harness):

    def test_inside_claude_code_the_live_config_is_refused_and_left_untouched(self):
        self.env(CLAUDECODE="1")
        with self.assertRaises(ValueError) as caught:
            repo_scan.write(self.live, self.ROWS)
        self.assertIn(repo_scan.MAIN_THREAD_ENV, str(caught.exception))
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))
        self.assertEqual([], self.leftovers())

    def test_a_blank_or_wrong_attestation_is_not_an_attestation(self):
        for value in ("", "0", "true", "yes", " 1"):
            self.env(CLAUDECODE="1", AGENTRY_MAIN_THREAD=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                repo_scan.write(self.live, self.ROWS)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))

    def test_any_non_empty_claude_marker_counts_as_inside_claude_code(self):
        for value in ("1", "true", "0"):
            self.env(CLAUDECODE=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                repo_scan.write(self.live, self.ROWS)

    def test_the_orchestrator_attests_the_main_thread_and_the_write_lands(self):
        self.env(CLAUDECODE="1", AGENTRY_MAIN_THREAD="1")
        repos, lanes = repo_scan.write(self.live, self.ROWS)
        self.assertIn("agentry", repos)
        self.assertEqual({"repo": "agentry"}, lanes["default"])
        self.assertEqual(["agentry"], list(json.loads(self.live.read_text("utf-8"))["repos"]))

    def test_a_persons_own_terminal_is_not_a_subagent(self):
        self.env()
        repo_scan.write(self.live, self.ROWS)
        self.assertIn("agentry", json.loads(self.live.read_text("utf-8"))["repos"])

    def test_any_other_file_is_written_inside_claude_code_without_attestation(self):
        self.env(CLAUDECODE="1")
        repo_scan.write(self.other, self.ROWS)
        self.assertIn("agentry", json.loads(self.other.read_text("utf-8"))["repos"])
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))

    def test_the_live_config_is_recognised_however_it_is_spelled(self):
        self.env(CLAUDECODE="1")
        spellings = [self.live,
                     self.root / "." / "pipeline.json",
                     self.root / "sub" / ".." / "pipeline.json",
                     Path(os.path.relpath(self.live))]
        (self.root / "sub").mkdir()
        for spelling in spellings:
            with self.subTest(spelling=str(spelling)), self.assertRaises(ValueError):
                repo_scan.write(spelling, self.ROWS)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))

    @unittest.skipUnless(os.name == "nt", "case-insensitive file names are a Windows rule")
    def test_a_different_casing_of_the_live_path_is_still_the_live_config(self):
        self.env(CLAUDECODE="1")
        with self.assertRaises(ValueError):
            repo_scan.write(self.root / "PIPELINE.JSON", self.ROWS)

    def test_a_path_that_cannot_be_compared_counts_as_the_live_one(self):
        self.env(CLAUDECODE="1")
        with unittest.mock.patch.object(os.path, "samefile", side_effect=OSError("boom")):
            with self.assertRaises(ValueError):
                repo_scan.write(self.other, self.ROWS)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.other))

    def test_the_cli_reports_the_refusal_with_exit_2_and_writes_nothing(self):
        self.env(CLAUDECODE="1")
        table = self.root / "table.json"
        table.write_text(json.dumps(self.ROWS), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        argv = ["repo_scan.py", "--root", str(self.root), "--table", str(table),
                "--write", str(self.live)]
        with (unittest.mock.patch.object(sys, "argv", argv),
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            code = repo_scan.main()
        self.assertEqual(2, code)
        self.assertEqual("", out.getvalue())
        self.assertIn("orchestrator", err.getvalue())
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))

    def test_the_cli_no_longer_accepts_an_abbreviated_write_flag(self):
        self.env()
        err = io.StringIO()
        argv = ["repo_scan.py", "--root", str(self.root), "--wr", str(self.live)]
        with (unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err),
              self.assertRaises(SystemExit) as caught):
            repo_scan.main()
        self.assertEqual(2, caught.exception.code)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.live))


class AtomicWriteTest(Harness):

    def setUp(self):
        super().setUp()
        self.env()

    def test_the_new_bytes_arrive_through_os_replace_from_a_temp_file_in_the_same_directory(self):
        real = os.replace
        seen = []

        def spy(src, dst):
            seen.append((Path(src), Path(dst), Path(src).read_bytes()))
            return real(src, dst)

        with unittest.mock.patch.object(os, "replace", side_effect=spy):
            repo_scan.write(self.other, self.ROWS)
        self.assertEqual(1, len(seen))
        src, dst, staged = seen[0]
        self.assertEqual(self.other.parent, src.parent)
        self.assertEqual(self.other, dst)
        self.assertNotEqual(src, dst)
        self.assertEqual(self.other.read_bytes(), staged)
        self.assertEqual([], self.leftovers())

    def test_the_target_is_never_opened_for_writing_in_place(self):
        opened = []
        real_open = Path.write_bytes

        def spy(path, data):
            opened.append(path)
            return real_open(path, data)

        with unittest.mock.patch.object(Path, "write_bytes", spy):
            repo_scan.write(self.other, self.ROWS)
        self.assertEqual([], opened)

    def test_a_failure_while_replacing_leaves_the_original_bytes_and_no_temp_file(self):
        with unittest.mock.patch.object(os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                repo_scan.write(self.other, self.ROWS)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.other))
        self.assertEqual([], self.leftovers())

    def test_a_failure_while_staging_leaves_the_original_bytes_and_no_temp_file(self):
        with unittest.mock.patch.object(os, "fsync", side_effect=OSError("no space")):
            with self.assertRaises(OSError):
                repo_scan.write(self.other, self.ROWS)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.other))
        self.assertEqual([], self.leftovers())

    def test_the_rollback_when_the_readers_drop_an_entry_is_atomic_too(self):
        replaced = []
        real = os.replace

        def spy(src, dst):
            replaced.append(Path(dst))
            return real(src, dst)

        with (unittest.mock.patch.object(repo_scan, "read_back", return_value=({}, {})),
              unittest.mock.patch.object(os, "replace", side_effect=spy)):
            with self.assertRaises(ValueError):
                repo_scan.write(self.other, self.ROWS)
        self.assertEqual([self.other, self.other], replaced)
        self.assertEqual(self.CONFIG.encode("utf-8"), self.bytes_of(self.other))
        self.assertEqual([], self.leftovers())


if __name__ == "__main__":
    unittest.main()

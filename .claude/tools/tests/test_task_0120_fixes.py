"""task-0120 fixer round: four small pins that had no test.

  * advance.py says the move was refused when a done/ copy sits next to the
    leftover active/ copy, instead of claiming the file is "already in done".
  * pretool_gate.is_bookkeeping derives from ORCH_ALLOW_PREFIXES (task-0119 AC2).
  * a command over the scan budget is denied by every agent profile with the
    "too complex to check" text, a plain unbalanced quote keeps its own text.
  * the events.py query CLI reports a corrupt events.db in one line, exit 2.
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
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import events
import pretool_gate
import state
import tmproot
from test_script_gate_evasions import PROFILES, run_profile

TASK = "task-0100"


class MoveRefusedMessageTest(unittest.TestCase):

    def setUp(self):
        tmp = tmproot.sandbox(self, "move_msg_")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            self.dirs[name] = tmp / name
            self.dirs[name].mkdir()
        patcher = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def finish(self) -> str:
        merged = [{"repo": "agentry", "branch": f"feature/{TASK}", "in_main": True}]
        with unittest.mock.patch.object(advance.git_state, "task_report", return_value=merged), \
                unittest.mock.patch.object(advance, "_merge_wait", side_effect=lambda t, r, w: r), \
                unittest.mock.patch.object(advance, "result") as result:
            advance.finish_or_wait_for_merge(TASK, {})
        return result.call_args.args[3]

    def put(self, folder: str, body: str) -> None:
        (self.dirs[folder] / f"{TASK}.md").write_text(f"---\nid: 100\n---\n{body}\n", encoding="utf-8")

    def test_a_leftover_active_copy_next_to_a_done_file_is_named_and_refused(self):
        self.put("active", "leftover")
        self.put("done", "record")
        said = self.finish()
        self.assertIn(f"tasks/active/{TASK}.md", said)
        self.assertIn(f"tasks/done/{TASK}.md", said)
        self.assertIn("refused", said)
        self.assertNotIn("already in tasks/done/", said)
        self.assertIn("record", (self.dirs["done"] / f"{TASK}.md").read_text(encoding="utf-8"))
        self.assertTrue((self.dirs["active"] / f"{TASK}.md").is_file())

    def test_a_task_that_is_only_in_done_still_reads_already_in_done(self):
        self.put("done", "record")
        self.assertIn("already in tasks/done/", self.finish())

    def test_a_clean_move_still_says_moved(self):
        self.put("active", "work")
        self.assertIn("moved to tasks/done/", self.finish())


class BookkeepingDerivesFromTheOrchestratorAllowlistTest(unittest.TestCase):

    def test_every_allowlist_prefix_is_bookkeeping(self):
        for prefix in pretool_gate.ORCH_ALLOW_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertTrue(pretool_gate.is_bookkeeping(f"{prefix}notes/a.md"))

    def test_a_path_outside_every_prefix_is_not_bookkeeping(self):
        self.assertFalse(pretool_gate.is_bookkeeping("src/app/main.py"))
        self.assertFalse(pretool_gate.is_bookkeeping("docs/../src/app/main.py"))

    def test_changing_the_allowlist_changes_the_answer(self):
        # A literal prefix tuple inside is_bookkeeping would keep answering for the
        # old trees; deriving from the shared constant follows the patched one.
        with unittest.mock.patch.object(pretool_gate, "ORCH_ALLOW_PREFIXES", ("zzz/",)):
            self.assertTrue(pretool_gate.is_bookkeeping("zzz/a.py"))
            self.assertFalse(pretool_gate.is_bookkeeping(".claude/a.py"))
            self.assertFalse(pretool_gate.is_bookkeeping("docs/a.md"))


class ScanBudgetDenyTextTest(unittest.TestCase):
    TOO_COMPLEX = '"$(' * 100
    UNBALANCED = "echo 'oops"

    def setUp(self):
        pretool_gate._too_complex.cache_clear()
        self.addCleanup(pretool_gate._too_complex.cache_clear)

    def test_every_profile_says_too_complex_to_check_for_a_command_over_the_budget(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                code, err = run_profile(profile, self.TOO_COMPLEX)
                self.assertEqual(2, code, err)
                self.assertIn("too complex to check", err)
                self.assertNotIn("unbalanced quote", err)

    def test_every_profile_keeps_the_quote_text_for_an_unbalanced_quote(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                code, err = run_profile(profile, self.UNBALANCED)
                self.assertEqual(2, code, err)
                self.assertIn("cannot be tokenised", err)
                self.assertNotIn("too complex", err)


class CorruptJournalCliTest(unittest.TestCase):

    def setUp(self):
        state_dir = tmproot.sandbox(self, "events_cli_") / "state"
        state_dir.mkdir()
        (state_dir / events.DB_NAME).write_bytes(b"this is not a sqlite database " * 20)
        patcher = unittest.mock.patch.object(events, "STATE_DIR", state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, argv: list) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = events.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_task_report_on_a_corrupt_journal_is_one_error_line_and_exit_2(self):
        code, out, err = self.run_cli(["--task", "task-0042"])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertEqual(1, len(err.strip().splitlines()))
        self.assertIn("events.py: cannot read", err)
        self.assertNotIn("Traceback", err)

    def test_summary_on_a_corrupt_journal_is_one_error_line_and_exit_2(self):
        code, out, err = self.run_cli(["--summary"])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertEqual(1, len(err.strip().splitlines()))


if __name__ == "__main__":
    unittest.main()

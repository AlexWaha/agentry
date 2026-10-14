"""A wrong lesson can be corrected in place and a disproven one withdrawn (task-0086).

The store could add a row and refuse a near-duplicate, but a lesson recorded
with a wrong claim kept ranking and kept being injected, and the refusal told
the caller to "refine the existing row" with no command to do it. These tests
pin --refine, --retire, the retrieval skip for retired rows, the migration of a
store created before the retired columns existed, and the refusal text.
"""

from __future__ import annotations

import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "memory"))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

import memory  # noqa: E402
import tmproot  # noqa: E402

WRONG = {
    "signature": "nested-attach-sits-behind-a-flag",
    "trigger": "reading nested rules files mid-session",
    "what": "the excludes setting governs the nested rules walk, measured",
    "why": "the probe was believed to discriminate",
    "fix": "treat the excludes setting as the switch for the nested walk",
    "area": "harness",
}

EM_DASH = chr(0x2014)


def run_cli(db, *argv) -> tuple:
    out = io.StringIO()
    with redirect_stdout(out):
        code = memory.main(["--db", str(db), *argv])
    return code, out.getvalue()


class CaseBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "memory_refine_")
        self.db = self.tmp / "memory.db"
        self.conn = memory.connect(self.db)
        self.addCleanup(self.conn.close)

    def titles(self, text):
        return [h["title"] for h in memory.query(text, conn=self.conn)]


class RefineTest(CaseBase):

    def test_refine_replaces_only_the_given_fields_and_keeps_the_row_id(self):
        rowid = memory.record_lesson(self.conn, **WRONG)
        got_id, changed = memory.refine_lesson(
            self.conn, signature=WRONG["signature"], fix="claim the effect, not the mechanism")
        self.assertEqual((got_id, changed), (rowid, ["fix"]))
        row = self.conn.execute("SELECT * FROM lessons WHERE id = ?", (rowid,)).fetchone()
        self.assertEqual(row["fix"], "claim the effect, not the mechanism")
        self.assertEqual(row["what"], WRONG["what"])
        self.assertEqual(memory.counts(self.conn)["lesson"], 1)

    def test_retrieval_ranks_the_corrected_text_and_drops_the_old(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.refine_lesson(self.conn, signature=WRONG["signature"],
                             fix="claim only the observed effect of the entry")
        self.assertEqual(self.titles("observed effect of the entry"), [WRONG["signature"]])
        self.assertEqual(self.titles("switch"), [])

    def test_refining_an_unknown_signature_is_refused(self):
        with self.assertRaises(memory.MemoryError_) as ctx:
            memory.refine_lesson(self.conn, signature="no-such-lesson", what="x")
        self.assertIn("no lesson", str(ctx.exception))

    def test_refine_without_any_field_is_refused(self):
        memory.record_lesson(self.conn, **WRONG)
        with self.assertRaises(memory.MemoryError_):
            memory.refine_lesson(self.conn, signature=WRONG["signature"])

    def test_refined_text_goes_through_the_same_validation_as_record(self):
        memory.record_lesson(self.conn, **WRONG)
        for bad in ("two\nlines", f"dash {EM_DASH} here", "x" * (memory.MAX_FIELD + 1)):
            with self.assertRaises(memory.MemoryError_):
                memory.refine_lesson(self.conn, signature=WRONG["signature"], what=bad)
        row = self.conn.execute("SELECT what FROM lessons").fetchone()
        self.assertEqual(row["what"], WRONG["what"])

    def test_agent_scoped_rows_are_addressed_by_signature_and_agent(self):
        memory.record_lesson(self.conn, **{**WRONG, "agent": "reviewer"})
        with self.assertRaises(memory.MemoryError_):
            memory.refine_lesson(self.conn, signature=WRONG["signature"], what="x")
        memory.refine_lesson(self.conn, signature=WRONG["signature"], agent="reviewer", what="y")
        self.assertEqual(self.conn.execute("SELECT what FROM lessons").fetchone()[0], "y")


class RetireTest(CaseBase):

    def test_retired_row_is_no_longer_returned_by_query(self):
        memory.record_lesson(self.conn, **WRONG)
        self.assertEqual(self.titles("nested rules walk excludes"), [WRONG["signature"]])
        memory.retire_lesson(self.conn, signature=WRONG["signature"],
                             reason="probe discriminated nothing")
        self.assertEqual(self.titles("nested rules walk excludes"), [])

    def test_retired_row_stays_in_the_table_with_its_reason(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.retire_lesson(self.conn, signature=WRONG["signature"],
                             reason="probe discriminated nothing")
        row = self.conn.execute("SELECT * FROM lessons").fetchone()
        self.assertEqual(row["retired_reason"], "probe discriminated nothing")
        self.assertTrue(row["retired"])
        self.assertEqual(row["what"], WRONG["what"])

    def test_retired_row_is_skipped_through_a_readonly_connection(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.retire_lesson(self.conn, signature=WRONG["signature"], reason="wrong")
        ro = memory.connect_readonly(self.db)
        self.addCleanup(ro.close)
        self.assertEqual(memory.query("nested rules walk excludes", conn=ro), [])

    def test_retire_needs_a_reason(self):
        memory.record_lesson(self.conn, **WRONG)
        with self.assertRaises(memory.MemoryError_):
            memory.retire_lesson(self.conn, signature=WRONG["signature"], reason="")
        self.assertEqual(self.titles("nested rules walk excludes"), [WRONG["signature"]])

    def test_retiring_twice_or_an_unknown_row_is_refused(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.retire_lesson(self.conn, signature=WRONG["signature"], reason="wrong")
        with self.assertRaises(memory.MemoryError_) as ctx:
            memory.retire_lesson(self.conn, signature=WRONG["signature"], reason="again")
        self.assertIn("already retired", str(ctx.exception))
        with self.assertRaises(memory.MemoryError_):
            memory.retire_lesson(self.conn, signature="no-such-lesson", reason="x")

    def test_a_retired_row_cannot_be_refined_back_to_life(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.retire_lesson(self.conn, signature=WRONG["signature"], reason="wrong")
        with self.assertRaises(memory.MemoryError_) as ctx:
            memory.refine_lesson(self.conn, signature=WRONG["signature"], what="still wrong")
        self.assertIn("new signature", str(ctx.exception))
        self.assertEqual(self.titles("nested rules walk excludes"), [])

    def test_export_lists_retired_rows_apart_from_live_lessons(self):
        memory.record_lesson(self.conn, **WRONG)
        memory.record_lesson(self.conn, **{**WRONG, "signature": "the-live-one"})
        memory.retire_lesson(self.conn, signature=WRONG["signature"],
                             reason="probe discriminated nothing")
        text = memory.export_markdown(self.conn)
        self.assertIn("### the-live-one", text)
        self.assertNotIn(f"### {WRONG['signature']}", text)
        self.assertIn(f"**{WRONG['signature']}** - retired", text)
        self.assertIn("probe discriminated nothing", text)


class OldStoreTest(CaseBase):

    def test_a_store_created_before_the_retired_columns_is_migrated_on_connect(self):
        old = self.tmp / "old.db"
        raw = sqlite3.connect(old)
        raw.executescript(memory.SCHEMA.replace(
            ",\n    retired        TEXT NOT NULL DEFAULT '',\n"
            "    retired_reason TEXT NOT NULL DEFAULT ''", ""))
        cols = {r[1] for r in raw.execute("PRAGMA table_info(lessons)")}
        raw.close()
        self.assertNotIn("retired", cols)
        conn = memory.connect(old)
        self.addCleanup(conn.close)
        memory.record_lesson(conn, **WRONG)
        memory.retire_lesson(conn, signature=WRONG["signature"], reason="wrong")
        self.assertEqual(memory.query("nested rules walk excludes", conn=conn), [])


class CliTest(CaseBase):

    RECORD = ("--record", "--kind", "lesson", "--signature", WRONG["signature"],
              "--trigger", WRONG["trigger"], "--what", WRONG["what"], "--why", WRONG["why"],
              "--fix", WRONG["fix"])

    def test_refine_output_says_refined_not_recorded(self):
        run_cli(self.db, *self.RECORD)
        code, out = run_cli(self.db, "--refine", "--signature", WRONG["signature"],
                            "--fix", "claim the effect only")
        self.assertEqual(code, 0)
        self.assertIn("REFINED lesson", out)
        self.assertNotIn("recorded", out)

    def test_retire_output_says_retired(self):
        memory.record_lesson(self.conn, **WRONG)
        code, out = run_cli(self.db, "--retire", "--signature", WRONG["signature"],
                            "--reason", "disproven")
        self.assertEqual(code, 0)
        self.assertIn("RETIRED lesson", out)

    def test_a_refused_refine_exits_nonzero_with_the_reason(self):
        code, out = run_cli(self.db, "--refine", "--signature", "no-such-lesson", "--what", "x")
        self.assertEqual(code, 1)
        self.assertIn("no lesson", out)

    def test_the_duplicate_refusal_names_the_commands_that_perform_the_action(self):
        run_cli(self.db, *self.RECORD)
        code, out = run_cli(self.db, *self.RECORD)
        self.assertEqual(code, 0)
        self.assertIn(f"memory.py --refine --signature {WRONG['signature']}", out)
        self.assertIn(f"memory.py --retire --signature {WRONG['signature']}", out)
        self.assertIn("--reason", out)

    def test_the_duplicate_refusal_carries_the_agent_for_agent_scoped_rows(self):
        args = (*self.RECORD, "--agent", "reviewer")
        run_cli(self.db, *args)
        _, out = run_cli(self.db, *args)
        self.assertIn(f"--refine --signature {WRONG['signature']} --agent reviewer", out)

    def test_a_duplicate_pattern_does_not_promise_a_refine_it_cannot_do(self):
        args = ("--record", "--kind", "pattern", "--name", "three-state-probe",
                "--use-when", "a probe is unsure")
        run_cli(self.db, *args)
        _, out = run_cli(self.db, *args)
        self.assertNotIn("--refine", out)


class Task0015ReconciliationTest(CaseBase):
    """The first use of the new operation: three contradicting rows become one."""

    def test_three_rows_reconcile_into_one_correct_row(self):
        first = "nested-attach-sits-behind-a-flag"
        second = "excludes-over-nested-walk-is-unmeasured"
        third = "excludes-do-filter-the-nested-walk-measured"
        for sig in (first, second, third):
            memory.record_lesson(self.conn, **{**WRONG, "signature": sig})
        memory.refine_lesson(
            self.conn, signature=first,
            what="an excludes entry added mid-session governed the very next nested read",
            fix="claim the effect observed; do not claim the mechanism or a cache rebuild")
        memory.retire_lesson(self.conn, signature=second, reason="superseded once the probe ran")
        memory.retire_lesson(self.conn, signature=third,
                             reason="overstates evidence: asserts the mechanism")
        self.assertEqual(self.titles("nested rules walk excludes"), [first])


if __name__ == "__main__":
    unittest.main()

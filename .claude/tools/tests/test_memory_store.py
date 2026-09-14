"""Memory is a queried store, not an injected file head.

Injection used to print the top N lines of a markdown layer, so an agent got
whatever sat at the top of the file (37k tokens of it, on a real project).
These tests pin the replacement: structured records only, ranked retrieval, a
migration that loses nothing, a post-task gate that counts real rows, and a
retrieval path that stays silent instead of blocking when the store is absent.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "memory"))
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

import codebase_sync
import inject
import memory
import tmproot
import update

LESSON = {
    "signature": "park-must-be-written-to-state",
    "trigger": "any code path that parks a task",
    "what": "advance.py returned park without writing awaiting_human, so stop_gate offered the next task",
    "why": "the JSON the model reads and the run row the hooks read are two different channels",
    "fix": "every park path must also set awaiting_human, and the unpark path must clear it",
}


class StoreTestCase(unittest.TestCase):
    """Each test gets its own store file; nothing touches the real memory.db."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "memory_store_")
        self.db = self.tmp / "memory.db"
        self._real_db = memory.DB_PATH
        memory.DB_PATH = self.db
        self.conn = memory.connect(self.db)
        self.addCleanup(self.conn.close)
        self.addCleanup(setattr, memory, "DB_PATH", self._real_db)


class RecordAndReadBackTest(StoreTestCase):

    def test_recorded_lesson_is_found_by_query(self):
        rowid = memory.record_lesson(self.conn, **LESSON)
        self.assertTrue(rowid)
        hits = memory.query("a task parked waiting on a merge", conn=self.conn)
        self.assertEqual([h["title"] for h in hits], [LESSON["signature"]])
        self.assertIn("awaiting_human", hits[0]["text"])

    def test_counts_report_rows_per_kind(self):
        memory.record_lesson(self.conn, **LESSON)
        memory.record_pattern(self.conn, name="three-state-probe",
                              use_when="a git probe can answer cannot tell")
        memory.record_module(self.conn, path=".claude/tools/memory",
                             responsibility="memory store, retrieval and gates")
        self.assertEqual(memory.counts(self.conn),
                         {"lesson": 1, "pattern": 1, "module": 1})

    def test_re_recording_the_same_signature_is_a_no_op(self):
        self.assertTrue(memory.record_lesson(self.conn, **LESSON))
        self.assertEqual(memory.record_lesson(self.conn, **LESSON), 0)
        self.assertEqual(memory.counts(self.conn)["lesson"], 1)

    def test_recording_a_known_module_path_updates_it(self):
        memory.record_module(self.conn, path="app/Services", responsibility="old")
        memory.record_module(self.conn, path="app/Services", responsibility="business logic")
        self.assertEqual(memory.counts(self.conn)["module"], 1)
        hits = memory.query("business logic services", conn=self.conn)
        self.assertIn("business logic", hits[0]["text"])
        self.assertNotIn("old", hits[0]["text"])

    def test_export_renders_every_kind(self):
        memory.record_lesson(self.conn, **LESSON)
        memory.record_pattern(self.conn, name="three-state-probe", use_when="probe is unsure")
        memory.record_module(self.conn, path="app/Services", responsibility="business logic")
        text = memory.export_markdown(self.conn)
        self.assertIn(LESSON["signature"], text)
        self.assertIn("three-state-probe", text)
        self.assertIn("app/Services", text)


class FreeFormProseIsRejectedTest(StoreTestCase):

    def test_missing_field_is_refused(self):
        payload = dict(LESSON, fix="")
        with self.assertRaises(memory.MemoryError_):
            memory.record_lesson(self.conn, **payload)

    def test_multi_line_field_is_refused(self):
        payload = dict(LESSON, what="first sentence\nsecond sentence")
        with self.assertRaises(memory.MemoryError_) as ctx:
            memory.record_lesson(self.conn, **payload)
        self.assertIn("single line", str(ctx.exception))

    def test_non_kebab_signature_is_refused(self):
        payload = dict(LESSON, signature="A whole sentence about what went wrong")
        with self.assertRaises(memory.MemoryError_):
            memory.record_lesson(self.conn, **payload)

    def test_em_dash_is_refused(self):
        # Built from the code point, not typed: this file is scanned by
        # DashTest in test_supervisor.py, which now derives its file list
        # from this directory, so a literal here would fail that scan.
        payload = dict(LESSON, why="two channels " + chr(0x2014) + " only the row is state")
        with self.assertRaises(memory.MemoryError_) as ctx:
            memory.record_lesson(self.conn, **payload)
        self.assertIn("dash", str(ctx.exception))

    def test_oversized_field_is_refused(self):
        payload = dict(LESSON, what="x" * (memory.MAX_FIELD + 1))
        with self.assertRaises(memory.MemoryError_):
            memory.record_lesson(self.conn, **payload)

    def test_nothing_was_written_by_a_refused_record(self):
        with self.assertRaises(memory.MemoryError_):
            memory.record_lesson(self.conn, **dict(LESSON, fix=""))
        self.assertEqual(memory.counts(self.conn)["lesson"], 0)


class RankingTest(StoreTestCase):

    def setUp(self):
        super().setUp()
        memory.record_lesson(self.conn, signature="sqlite-fts-tokenizer",
                             trigger="querying the memory store",
                             what="an FTS5 MATCH built from raw prose raised a syntax error",
                             why="MATCH takes a query language, not a text box",
                             fix="build the query from content words, quoted and ORed")
        memory.record_lesson(self.conn, signature="docker-volume-permissions",
                             trigger="mounting a host directory into a container",
                             what="the container wrote files owned by root on the host",
                             why="the image ran as root and the mount inherits its uid",
                             fix="run the image as a non-root user matching the host uid")

    def test_the_relevant_row_ranks_first(self):
        hits = memory.query("fts5 match query tokenizer sqlite", conn=self.conn)
        self.assertEqual(hits[0]["title"], "sqlite-fts-tokenizer")
        hits = memory.query("docker container mount host root user", conn=self.conn)
        self.assertEqual(hits[0]["title"], "docker-volume-permissions")

    def test_limit_is_honoured(self):
        self.assertEqual(len(memory.query("container sqlite", limit=1, conn=self.conn)), 1)

    def test_kind_filter_excludes_other_kinds(self):
        memory.record_pattern(self.conn, name="sqlite-store", use_when="fts5 retrieval is needed")
        titles = [h["title"] for h in memory.query("sqlite fts5", kinds=["pattern"],
                                                   conn=self.conn)]
        self.assertEqual(titles, ["sqlite-store"])

    def test_query_with_no_content_words_returns_nothing(self):
        self.assertEqual(memory.query("of the and to", conn=self.conn), [])


class MigrationTest(StoreTestCase):

    AGENT_MD = """# devops-engineer - persistent memory

Format per lesson:

## [SIGNATURE / TRIGGER]
- **What:** one sentence - the mistake or surprise
- **Why:** one sentence - root cause
- **Fix:** imperative, specific, checkable rule
- **Date:** YYYY-MM-DD

## [substring-match-of-a-cli-subcommand / writing any gate or CI guard]
- **What:** the gates matched `"git push" in command`, so `git -C dir push` skipped every check.
- **Why:** a substring assumes the subcommand is adjacent to the binary name.
- **Fix:** tokenise with shlex and walk argv instead of matching substrings.
- **Date:** 2026-09-13

## park-must-be-written-to-state
- **What:** advance.py parked a task without writing awaiting_human.
- **Why:** the JSON and the run row are two different channels.
- **Fix:** every park path must also set awaiting_human.
- **Date:** 2026-09-13
"""

    FRONTMATTER_MD = """---
name: project-agentry-overhaul
description: This repo is the harness itself, so plan items map to .claude/tools files
metadata:
  type: project
---

The product under specification IS the `.claude/` tooling itself.

**Why:** the harness executes the plan that repairs the harness.

**How to apply:** verify every claim against `.claude/tools/` before writing an FR.
"""

    def test_agent_memory_parse_skips_the_template_block(self):
        found = memory.parse_agent_memory(self.AGENT_MD)
        self.assertEqual([f["signature"] for f in found],
                         ["substring-match-of-a-cli-subcommand", "park-must-be-written-to-state"])

    def test_signature_keeps_its_path_like_tail(self):
        found = memory.parse_agent_memory(
            "## [a-rule-written-in-rules/*.md but not in code / any safety boundary]\n"
            "- **What:** the rule was documented and never enforced.\n"
            "- **Why:** documenting a change reads like doing it.\n"
            "- **Fix:** land the constant in the same task that documents it.\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["trigger"], "any safety boundary")
        self.assertTrue(found[0]["signature"].startswith("a-rule-written-in-rules"))

    def test_every_parsed_lesson_records_and_round_trips(self):
        for lesson in memory.parse_agent_memory(self.AGENT_MD):
            memory.record_lesson(self.conn, agent="devops-engineer", created=lesson["date"],
                                 **{k: v for k, v in lesson.items() if k != "date"})
        self.assertEqual(memory.counts(self.conn)["lesson"], 2)
        hits = memory.query("substring subcommand shlex argv", conn=self.conn)
        self.assertEqual(hits[0]["title"], "substring-match-of-a-cli-subcommand")

    def test_frontmatter_memory_maps_onto_a_lesson(self):
        parsed = memory.parse_frontmatter_memory(self.FRONTMATTER_MD, "fallback")
        self.assertEqual(parsed["signature"], "project-agentry-overhaul")
        self.assertIn("harness", parsed["trigger"])
        self.assertIn("tooling", parsed["what"])
        self.assertIn("repairs the harness", parsed["why"])
        self.assertIn("verify every claim", parsed["fix"])
        self.assertTrue(memory.record_lesson(self.conn, agent="spec-developer",
                                             **{k: v for k, v in parsed.items()
                                                if k != "date"}))


class PostTaskGateTest(StoreTestCase):

    def setUp(self):
        super().setUp()
        self.stamps = self.tmp / "stamps"
        self._real_stamps = update.STAMP_DIR
        update.STAMP_DIR = self.stamps
        self.addCleanup(setattr, update, "STAMP_DIR", self._real_stamps)

    def stamp(self, task: str, extra=()):
        out = io.StringIO()
        with redirect_stdout(out):
            code = update.main(["--stamp", "--task", task, *extra])
        return code, out.getvalue()

    def test_stamp_is_refused_when_the_store_gained_no_rows(self):
        memory.record_lesson(self.conn, **LESSON)
        code, out = self.stamp("task-0100")
        self.assertEqual(code, 0, out)          # first stamp has no baseline to compare
        code, out = self.stamp("task-0101")
        self.assertEqual(code, 1)
        self.assertIn("gained no rows", out)
        self.assertFalse((self.stamps / "task-0101.json").is_file())

    def test_stamp_passes_after_a_new_row_is_recorded(self):
        memory.record_lesson(self.conn, **LESSON)
        self.stamp("task-0100")
        memory.record_pattern(self.conn, name="three-state-probe", use_when="probe is unsure")
        code, out = self.stamp("task-0101")
        self.assertEqual(code, 0, out)
        self.assertIn("1 new row", out)
        stamped = json.loads((self.stamps / "task-0101.json").read_text(encoding="utf-8"))
        self.assertEqual(stamped["counts"], {"lesson": 1, "pattern": 1, "module": 0})

    def test_none_stamps_without_any_new_row(self):
        memory.record_lesson(self.conn, **LESSON)
        self.stamp("task-0100")
        code, out = self.stamp("task-0101", ["--none"])
        self.assertEqual(code, 0, out)
        self.assertTrue((self.stamps / "task-0101.json").is_file())

    def test_a_missing_store_cannot_block_a_stamp(self):
        memory.DB_PATH = self.tmp / "absent.db"
        code, out = self.stamp("task-0102")
        self.assertEqual(code, 0, out)

    def test_unstamped_done_task_is_reported_as_debt(self):
        debt = update.unstamped_done_tasks()
        self.assertIsInstance(debt, list)       # shape contract used by stop_gate.py


class InjectionTest(StoreTestCase):

    def run_inject(self, payload, argv):
        real_stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = inject.main(argv)
        finally:
            sys.stdin = real_stdin
        return code, out.getvalue()

    def test_matching_rows_are_injected_with_their_count(self):
        memory.record_lesson(self.conn, **LESSON)
        code, out = self.run_inject({"prompt": "the task parked on a merge and stop_gate "
                                               "offered the next backlog task"},
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("1 row(s) matched this dispatch", out)
        self.assertIn(LESSON["signature"], out)

    def test_empty_store_injects_nothing(self):
        code, out = self.run_inject({"prompt": "implement the memory store"},
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_store_injects_nothing_and_exits_zero(self):
        code, out = self.run_inject({"prompt": "implement the memory store"},
                                    ["--db", str(self.tmp / "absent.db")])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_corrupt_store_injects_nothing_and_exits_zero(self):
        corrupt = self.tmp / "corrupt.db"
        corrupt.write_bytes(b"this is not a database")
        code, out = self.run_inject({"prompt": "implement the memory store"},
                                    ["--db", str(corrupt)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_unparseable_payload_injects_nothing(self):
        real_stdin, sys.stdin = sys.stdin, io.StringIO("not json at all")
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = inject.main(["--db", str(self.db)])
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_injection_stays_under_the_byte_budget(self):
        filler = ("the gate hook branch push commit approval pipeline stage task memory "
                  "store retrieval injection budget lesson pattern module ") * 6
        for i in range(40):
            memory.record_lesson(self.conn, signature=f"budget-lesson-{i:02d}",
                                 trigger=f"dispatching any agent {filler}",
                                 what=f"row {i} {filler}", why=f"cause {i} {filler}",
                                 fix=f"rule {i} {filler}")
        code, out = self.run_inject({"prompt": "gate hook branch push commit approval "
                                               "pipeline stage task memory store"},
                                    ["--db", str(self.db), "--limit", "40"])
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out.encode("utf-8")), inject.BUDGET_BYTES + 1)
        # Trimming is stated, not hidden: the head says how many of the matches
        # actually travelled.
        self.assertRegex(out, r"## Project memory: \d+ of 40 matching row\(s\) \(byte budget\)")


class ModuleMapSyncTest(StoreTestCase):

    def test_check_asks_for_module_rows_when_the_store_has_none(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = codebase_sync.check()
        self.assertEqual(code, 1)
        self.assertIn("no module rows", out.getvalue())

    def test_heads_round_trip_through_the_store_meta_table(self):
        memory.meta_set(self.conn, codebase_sync.META_KEY, {".": "abc123"})
        self.assertEqual(codebase_sync.stored_heads(), {".": "abc123"})

    def test_map_is_not_empty_once_a_module_row_exists(self):
        memory.record_module(self.conn, path="app/Services", responsibility="business logic")
        self.assertFalse(codebase_sync.map_is_empty())


if __name__ == "__main__":
    unittest.main()

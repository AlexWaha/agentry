"""Memory is a queried store, not an injected file head.

Injection used to print the top N lines of a markdown layer, so an agent got
whatever sat at the top of the file (37k tokens of it, on a real project).
These tests pin the replacement: structured records only, ranked retrieval, a
migration that loses nothing, a post-task gate that counts real rows, and a
retrieval path that stays silent instead of blocking when the store is absent.
"""

from __future__ import annotations

import importlib.util
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
import state
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


# What the live SubagentStart payload actually holds: the session fields plus
# hook_event_name, agent_id and agent_type. Read out of the 2.1.269 binary
# (`{...session, hook_event_name:"SubagentStart", agent_id:n, agent_type:r}`)
# and confirmed against inject.payload_text() before it was deleted, which
# returned "" for every dispatch this project has ever made. No prompt, no
# description, no task field - so the payload is NOT the query, and these tests
# feed a realistic one rather than the invented {"prompt": ...} they used to.
LIVE_PAYLOAD = {
    "session_id": "0198f2c4-0000-7000-8000-000000000001",
    "transcript_path": "C:/Users/dev/.claude/projects/x/session.jsonl",
    "cwd": "E:/Personal/AI-team-universal",
    "permission_mode": "bypassPermissions",
    "hook_event_name": "SubagentStart",
    "agent_id": "agent_01",
    "agent_type": "senior-backend-dev",
}


class InjectTestCase(StoreTestCase):
    """Adds the stdin-driven call into inject.main(). No tests of its own, so
    the classes below inherit the helper without re-running anything.

    Every injection test reads a pipeline config this class WRITES, never the
    shipped .agentry/pipeline.json. A test that passes because of how the
    shipped config happens to be set proves nothing about the code: that is
    exactly how gates.forbid_dev_null sat switched off for six days with five
    tests reporting green (task-0070). Absent keys are the default state here,
    so the default-value tests are genuinely testing the fallback.

    inject.ACTIVE_DIR is redirected into the sandbox for the same reason: the
    query source is the active task file(s), so a test left pointing at the
    real .agentry/tasks/active/ would query whatever the repo happens to be
    working on that day.
    """

    def setUp(self):
        super().setUp()
        self.pipeline_path = self.tmp / "pipeline.json"
        self._real_pipeline = state.PIPELINE_PATH
        state.PIPELINE_PATH = self.pipeline_path
        self.addCleanup(setattr, state, "PIPELINE_PATH", self._real_pipeline)
        self.active = self.tmp / "active"
        self.active.mkdir()
        self._real_active = inject.ACTIVE_DIR
        inject.ACTIVE_DIR = self.active
        self.addCleanup(setattr, inject, "ACTIVE_DIR", self._real_active)
        self.write_cfg({})

    def write_cfg(self, memory_block) -> None:
        self.pipeline_path.write_text(json.dumps({"memory": memory_block}),
                                      encoding="utf-8")

    def fill_store(self, count: int = 40) -> None:
        """A corpus fat enough that the block budget has to drop rows and the
        row cap has to truncate. Every row matches the query below."""
        filler = ("the gate hook branch push commit approval pipeline stage task memory "
                  "store retrieval injection budget lesson pattern module ") * 6
        for i in range(count):
            memory.record_lesson(self.conn, signature=f"budget-lesson-{i:02d}",
                                 trigger=f"dispatching any agent {filler}",
                                 what=f"row {i} {filler}", why=f"cause {i} {filler}",
                                 fix=f"rule {i} {filler}")

    def inject_corpus(self):
        return self.run_inject("gate hook branch push commit approval "
                               "pipeline stage task memory store",
                               ["--db", str(self.db), "--limit", "40"])

    def body_rows(self, out: str) -> list:
        return [ln for ln in self.delivered(out).splitlines() if ln.startswith("- [")]

    def delivered(self, out: str) -> str:
        """The text that actually reaches the agent: the block inside the
        envelope. Nothing emitted means nothing delivered."""
        if not out.strip():
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def run_inject(self, query, argv, payload=None, stdin=None):
        """`query` is written to the active task file, because that is where
        the query comes from. `payload` only travels down stdin and is expected
        to change nothing."""
        (self.active / "task-9999.md").write_text(query, encoding="utf-8")
        text = stdin if stdin is not None else json.dumps(payload or LIVE_PAYLOAD)
        real_stdin, sys.stdin = sys.stdin, io.StringIO(text)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = inject.main(argv)
        finally:
            sys.stdin = real_stdin
        return code, out.getvalue()


class InjectionDeliveryTest(InjectTestCase):
    """task-0081. Claude Code DISCARDS plain stdout on SubagentStart; only
    hookSpecificOutput.additionalContext reaches the agent. inject.py printed
    the block raw from the day it was written, so 100 lessons, 2 patterns and 9
    module rows were delivered to nobody, silently, at exit 0 - and every
    static test in this file passed throughout, because they all read stdout
    directly. THIS is the test that would have caught it. Reverting the
    json.dumps envelope in inject.main() must fail the first test below with
    "stdout is not JSON, so SubagentStart discards it".
    """

    def test_a_non_empty_result_is_emitted_as_a_subagentstart_envelope(self):
        memory.record_lesson(self.conn, **LESSON)
        code, out = self.run_inject("the task parked on a merge and stop_gate "
                                    "offered the next backlog task",
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip(), "nothing was emitted at all")
        try:
            obj = json.loads(out)
        except ValueError as exc:
            self.fail(f"stdout is not JSON, so SubagentStart discards it: {exc}\n"
                      f"stdout began: {out[:120]!r}")
        self.assertEqual(list(obj), ["hookSpecificOutput"])  # exactly one object, one key
        self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertIn(LESSON["signature"], obj["hookSpecificOutput"]["additionalContext"])

    def test_zero_matches_emit_nothing_rather_than_an_empty_envelope(self):
        memory.record_lesson(self.conn, **LESSON)
        code, out = self.run_inject("xylophone quokka zeppelin narwhal",
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_the_envelope_is_ascii_even_when_the_matched_row_is_not(self):
        # The real store holds arrows and Cyrillic. On a cp1252 stdout an
        # unescaped one raises UnicodeEncodeError into main()'s blanket
        # handler, which is the same silent no-op this task is fixing, so
        # ensure_ascii is load-bearing rather than cosmetic.
        memory.record_lesson(self.conn, signature="a-row-with-non-ascii-text",
                             trigger="a lesson recorded with an arrow in it",
                             what="the park merge stop_gate backlog path \u2192 nothing",
                             # Escaped, not literal: ruff RUF001 reads a bare
                             # Cyrillic letter as an ambiguous look-alike, the
                             # same reason memory.py escapes its dash class.
                             why="\u041a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430"
                                 " reached a cp1252 stdout",
                             fix="emit via json.dumps with the default ensure_ascii")
        code, out = self.run_inject("the task parked on a merge and stop_gate "
                                    "offered the next backlog task",
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii(), "stdout carries raw non-ASCII")
        self.assertIn("\\u2192", out)                     # escaped, not dropped
        block = self.delivered(out)
        self.assertIn("\u2192", block)                    # and it round-trips
        self.assertIn("\u041a", block)

    def test_matching_rows_are_injected_with_their_count(self):
        memory.record_lesson(self.conn, **LESSON)
        code, out = self.run_inject("the task parked on a merge and stop_gate "
                                    "offered the next backlog task",
                                    ["--db", str(self.db)])
        self.assertEqual(code, 0)
        block = self.delivered(out)
        self.assertIn("1 row(s) matched the active task", block)
        self.assertIn(LESSON["signature"], block)

    def test_empty_store_injects_nothing(self):
        code, out = self.run_inject("implement the memory store", ["--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_store_injects_nothing_and_exits_zero(self):
        code, out = self.run_inject("implement the memory store",
                                    ["--db", str(self.tmp / "absent.db")])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_corrupt_store_injects_nothing_and_exits_zero(self):
        corrupt = self.tmp / "corrupt.db"
        corrupt.write_bytes(b"this is not a database")
        code, out = self.run_inject("implement the memory store", ["--db", str(corrupt)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_injection_stays_under_the_byte_budget(self):
        self.fill_store()
        code, out = self.inject_corpus()
        self.assertEqual(code, 0)
        self.assertLessEqual(len(self.delivered(out).encode("utf-8")),
                             inject.DEFAULT_BUDGET_BYTES + 1)
        # Trimming is stated, not hidden: the head says how many of the matches
        # actually travelled.
        self.assertRegex(self.delivered(out),
                         r"## Project memory: \d+ of 40 matching row\(s\) \(byte budget\)")


class InjectionQuerySourceTest(InjectTestCase):
    """task-0081, second half. The query is the active task file, and the
    payload steers nothing - it cannot, it carries no text. These tests pin
    that so nobody re-adds a payload scavenger whose silent "" return is what
    hid the delivery defect for as long as it lasted.
    """

    def setUp(self):
        super().setUp()
        memory.record_lesson(self.conn, **LESSON)

    def test_the_active_task_file_is_the_query(self):
        _, out = self.run_inject("the task parked on a merge and stop_gate "
                                 "offered the next backlog task",
                                 ["--db", str(self.db)])
        self.assertIn(LESSON["signature"], self.delivered(out))
        self.assertIn("against the active task file(s)", self.delivered(out))

    def test_a_prompt_in_the_payload_does_not_steer_the_query(self):
        # A payload spelling that the deleted QUERY_KEYS would have preferred.
        # It names words no row holds, while the task file names the match: if
        # the payload were consulted, nothing would be injected.
        _, out = self.run_inject("the task parked on a merge and stop_gate "
                                 "offered the next backlog task",
                                 ["--db", str(self.db)],
                                 payload={**LIVE_PAYLOAD,
                                          "prompt": "xylophone quokka zeppelin"})
        self.assertIn(LESSON["signature"], self.delivered(out))

    def test_an_unparseable_payload_no_longer_silences_injection(self):
        # It used to: the payload was parsed for a query, so garbage stdin
        # meant an empty query. Now stdin is drained and discarded.
        code, out = self.run_inject("the task parked on a merge and stop_gate "
                                    "offered the next backlog task",
                                    ["--db", str(self.db)], stdin="not json at all")
        self.assertEqual(code, 0)
        self.assertIn(LESSON["signature"], self.delivered(out))

    def test_a_stdin_that_raises_on_read_still_injects(self):
        # The concrete input for main()'s stdin handler: a hook spawned with a
        # closed stdin. Without that try/except this raises past everything.
        class ClosedStdin(io.StringIO):
            def isatty(self) -> bool:
                return False

            def read(self, *args) -> str:
                raise OSError("stdin is closed")

        (self.active / "task-9999.md").write_text(
            "the task parked on a merge and stop_gate offered the next backlog task",
            encoding="utf-8")
        real_stdin, sys.stdin = sys.stdin, ClosedStdin()
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = inject.main(["--db", str(self.db)])
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 0)
        self.assertIn(LESSON["signature"], self.delivered(out.getvalue()))

    def test_no_active_task_file_injects_nothing(self):
        for path in self.active.glob("*"):
            path.unlink()
        real_stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(LIVE_PAYLOAD))
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = inject.main(["--db", str(self.db)])
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_a_db_path_that_cannot_even_be_stat_ed_exits_zero_emitting_nothing(self):
        # The concrete input for main()'s blanket handler, named per NFR-4
        # rather than asserted from reading: connect_readonly() calls
        # Path.is_file() OUTSIDE its own try, and an embedded null byte makes
        # that raise ValueError, which nothing below main() catches.
        code, out = self.run_inject("the task parked on a merge and stop_gate "
                                    "offered the next backlog task",
                                    ["--db", "bad\x00path.db"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class InjectionBudgetIsConfigTest(InjectTestCase):
    """FR-23 / contract C-5: both caps live in .agentry/pipeline.json, and
    turning either dial changes the emitted bytes with no code edit.

    Every measurement here is of the BLOCK INSIDE the envelope, which is what
    inject_budget_bytes caps (task-0081). See
    test_the_budget_caps_the_block_and_not_the_whole_emitted_object below for
    why that boundary was drawn there and not around the JSON.
    """

    def setUp(self):
        super().setUp()
        self.fill_store()

    def test_absent_keys_use_the_shipped_defaults(self):
        _, out = self.inject_corpus()
        size = len(self.delivered(out).encode("utf-8"))
        self.assertLessEqual(size, inject.DEFAULT_BUDGET_BYTES + 1)
        # Headroom check: the corpus must actually reach toward the default,
        # or the lowered-budget test below would prove nothing.
        self.assertGreater(size, 2000)

    def test_the_budget_caps_the_block_and_not_the_whole_emitted_object(self):
        # The envelope adds its scaffolding plus two bytes for every newline it
        # escapes, so the object is always larger than the block. Capping the
        # object instead would shrink the delivered text by an amount that
        # depends on how many newlines and non-ASCII characters the matched
        # rows happen to hold, and would quietly invalidate task-0011's
        # measurement of 3565 against this same 3800.
        # row_chars 120 packs the block tight against a 2000-byte budget:
        # measured at 1998 bytes in 11 rows, with the object at 2093. Two bytes
        # of headroom is what makes the two readings distinguishable - on the
        # rejected reading at least one of those rows would be dropped.
        self.write_cfg({"inject_budget_bytes": 2000, "inject_row_chars": 120})
        _, out = self.inject_corpus()
        block = len(self.delivered(out).encode("utf-8"))
        whole = len(out.strip().encode("utf-8"))
        self.assertLessEqual(block, 2000 + 1)
        self.assertGreater(whole, 2000,
                           f"the object fits the budget too ({whole} bytes), so this "
                           f"test cannot tell the two readings apart - pack the block "
                           f"tighter against the cap")
        self.assertGreater(whole, block)

    def test_lowering_the_block_budget_shortens_the_block(self):
        _, before = self.inject_corpus()
        self.write_cfg({"inject_budget_bytes": 2000})
        _, after = self.inject_corpus()
        self.assertLessEqual(len(self.delivered(after).encode("utf-8")), 2000 + 1)
        self.assertLess(len(self.delivered(after).encode("utf-8")),
                        len(self.delivered(before).encode("utf-8")))
        self.assertLess(len(self.body_rows(after)), len(self.body_rows(before)))
        self.assertRegex(self.delivered(after),
                         r"\d+ of 40 matching row\(s\) \(byte budget\)")

    def test_lowering_the_row_cap_shortens_the_longest_row(self):
        _, before = self.inject_corpus()
        self.assertGreater(max(len(r) for r in self.body_rows(before)), 200)
        self.write_cfg({"inject_row_chars": 200})
        _, after = self.inject_corpus()
        rows = self.body_rows(after)
        self.assertTrue(rows)
        # The cap applies to the row TEXT; the prefix (kind and title) and the
        # truncation marker sit outside it.
        for row in rows:
            text = row.split(": ", 1)[1]
            self.assertLessEqual(len(text.removesuffix(" ...")), 200)
        self.assertLess(max(len(r) for r in rows),
                        max(len(r) for r in self.body_rows(before)))
        # A shorter row means more rows fit the unchanged block budget.
        self.assertGreater(len(rows), len(self.body_rows(before)))

    def test_raising_the_block_budget_lets_more_rows_through(self):
        _, before = self.inject_corpus()
        self.write_cfg({"inject_budget_bytes": 12000})
        _, after = self.inject_corpus()
        self.assertGreater(len(self.body_rows(after)), len(self.body_rows(before)))
        self.assertLessEqual(len(self.delivered(after).encode("utf-8")), 12000 + 1)

    def test_unusable_values_fall_back_to_the_default_and_still_emit(self):
        _, default_out = self.inject_corpus()
        default_size = len(self.delivered(default_out).encode("utf-8"))
        for bad in (None, "2000", 0, -5, True, False, 1.5, [2000], {"bytes": 2000}, ""):
            with self.subTest(value=bad):
                self.write_cfg({"inject_budget_bytes": bad, "inject_row_chars": bad})
                _, out = self.inject_corpus()
                self.assertTrue(out.strip(), f"{bad!r} emitted nothing")
                self.assertEqual(len(self.delivered(out).encode("utf-8")), default_size)

    def test_a_non_dict_memory_block_falls_back(self):
        self.pipeline_path.write_text(json.dumps({"memory": "on"}), encoding="utf-8")
        _, out = self.inject_corpus()
        self.assertTrue(out.strip())
        self.assertEqual(inject.cfg_int("inject_budget_bytes", 4242), 4242)

    def test_an_unparseable_config_falls_back(self):
        self.pipeline_path.write_text("{ not json", encoding="utf-8")
        _, out = self.inject_corpus()
        self.assertTrue(out.strip())
        self.assertEqual(inject.cfg_int("inject_row_chars", 4242), 4242)

    def test_a_config_whose_top_level_is_not_an_object_falls_back(self):
        # load_pipeline() returns whatever the file parsed to, so a top-level
        # array reaches .get() and raises AttributeError. This is the ONLY
        # path into cfg_int's except clause: without this case that clause is
        # dead in the suite - replacing its `return default` with `raise`
        # leaves every other injection test green (measured during the test
        # stage of task-0011), which means nothing would notice if the
        # fail-open half of NFR-4 were deleted.
        self.pipeline_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        _, out = self.inject_corpus()
        self.assertTrue(out.strip())
        self.assertEqual(inject.cfg_int("inject_budget_bytes", 4242), 4242)

    def test_a_missing_config_file_falls_back(self):
        state.PIPELINE_PATH = self.tmp / "absent.json"
        _, out = self.inject_corpus()
        self.assertTrue(out.strip())
        self.assertEqual(inject.cfg_int("inject_budget_bytes", 4242), 4242)

    def test_a_tiny_but_valid_budget_is_honoured_and_still_emits_one_row(self):
        # The deliberate choice: 10 is absurdly small but it is a legal byte
        # count, so it is obeyed rather than replaced by the default. render()
        # never drops the last row, so the floor is the head line plus one
        # row - the block cannot be emptied by this key.
        self.write_cfg({"inject_budget_bytes": 10, "inject_row_chars": 40})
        _, out = self.inject_corpus()
        block = self.delivered(out)
        self.assertEqual(len(self.body_rows(out)), 1)
        self.assertIn("## Project memory:", block)
        self.assertRegex(block, r"1 of 40 matching row\(s\) \(byte budget\)")
        self.assertGreater(len(block.encode("utf-8")), 10)  # the floor, not the cap

    def test_the_count_line_says_matched_when_nothing_was_dropped(self):
        # Criterion 5, the regression check: the two spellings of the count
        # line still follow whether the budget trimmed anything.
        self.write_cfg({"inject_budget_bytes": 200000})
        _, out = self.inject_corpus()
        self.assertEqual(len(self.body_rows(out)), 40)
        self.assertIn("40 row(s) matched the active task", self.delivered(out))
        self.assertNotIn("byte budget", self.delivered(out))


class InjectSysPathOrderTest(unittest.TestCase):
    """inject.py's own dir must land ahead of tools/pipeline/ in sys.path.

    The inserts are LIFO, so the SECOND one wins index 0. update.py and
    codebase_sync.py both insert pipeline first and their own dir second;
    inject.py once had them reversed, which would let a future
    pipeline/memory.py shadow tools/memory/memory.py - and the resulting
    AttributeError is swallowed by main()'s blanket except, so injection would
    die silently with exit code 0. The module is re-executed here because the
    live sys.path is shared with every other test module.
    """

    def test_own_directory_precedes_the_pipeline_directory(self) -> None:
        memory_dir = str(TOOLS_DIR / "memory")
        pipeline_dir = str(TOOLS_DIR / "pipeline")
        saved = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), saved)
        sys.path[:] = [p for p in sys.path if p not in (memory_dir, pipeline_dir)]

        spec = importlib.util.spec_from_file_location(
            "inject_syspath_order", TOOLS_DIR / "memory" / "inject.py")
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

        self.assertLess(
            sys.path.index(memory_dir), sys.path.index(pipeline_dir),
            "inject.py must insert tools/pipeline FIRST and its own tools/memory "
            "SECOND, so its own dir ends up ahead of pipeline in sys.path; the "
            "two inserts are LIFO and look inverted.")


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

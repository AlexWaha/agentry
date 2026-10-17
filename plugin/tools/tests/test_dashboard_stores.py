"""stores.py: the read-only data access of the dashboard (spec-0002 FR-1 to FR-3, FR-10, FR-12, FR-15).

Everything runs on sandbox folders (tmproot.sandbox); no harness module is needed here.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = TESTS_DIR.parent / "dashboard"
for _dir in (DASHBOARD_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import stores
import tmproot


def make_db(path: Path, rows: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()
    return path


class RoConnectTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_stores_")

    def test_a_write_statement_through_the_connection_is_refused(self):
        db = make_db(self.root / "real.db")
        conn = stores.ro_connect(db)
        self.addCleanup(conn.close)
        for statement in ("INSERT INTO t (v) VALUES ('x')", "UPDATE t SET v = 'y'",
                          "DELETE FROM t", "CREATE TABLE u (a)"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.OperationalError):
                conn.execute(statement)

    def test_rows_are_readable_by_column_name(self):
        conn = stores.ro_connect(make_db(self.root / "real.db"))
        self.addCleanup(conn.close)
        self.assertEqual("v0", conn.execute("SELECT v FROM t").fetchone()["v"])

    def test_a_missing_store_is_none_and_nothing_is_created(self):
        missing = self.root / "not-yet" / "missing.db"
        self.assertIsNone(stores.ro_connect(missing))
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_a_directory_is_not_a_store(self):
        self.assertIsNone(stores.ro_connect(self.root))

    def test_the_context_manager_yields_none_for_an_absent_store(self):
        with stores.store(self.root / "absent.db") as conn:
            self.assertIsNone(conn)

    def test_the_context_manager_closes_the_connection(self):
        with stores.store(make_db(self.root / "real.db")) as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM t").fetchone()[0])
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_the_connection_is_closed_even_when_the_body_raises(self):
        db = make_db(self.root / "real.db")
        with self.assertRaises(RuntimeError), stores.store(db) as conn:
            raise RuntimeError("boom")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_the_file_can_be_renamed_and_renamed_back_after_use(self):
        # On Windows a leaked handle makes os.replace fail (spec FR-3 / NFR-13).
        db = make_db(self.root / "real.db")
        with stores.store(db) as conn:
            conn.execute("SELECT * FROM t").fetchall()
        moved = db.with_name("moved.db")
        os.replace(db, moved)
        os.replace(moved, db)
        self.assertTrue(db.is_file())

    def test_a_non_sqlite_file_fails_on_first_use_not_on_open(self):
        junk = self.root / "junk.db"
        junk.write_bytes(b"this is not a database" * 100)
        with stores.store(junk) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("SELECT * FROM sqlite_master").fetchall()

    def test_has_table(self):
        with stores.store(make_db(self.root / "real.db")) as conn:
            self.assertTrue(stores.has_table(conn, "t"))
            self.assertFalse(stores.has_table(conn, "nope"))


class LaneDiscoveryTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_lanes_")
        self.state = self.root / ".agentry" / "state"
        self.state.mkdir(parents=True)

    def touch(self, *names: str) -> None:
        for name in names:
            (self.state / name).write_bytes(b"")

    def test_run_db_is_default_and_run_lane_db_is_the_lane(self):
        self.touch("run.db", "run.fix.db")
        self.assertEqual({"default": self.state / "run.db", "fix": self.state / "run.fix.db"},
                         stores.lane_db_paths(self.root))

    def test_backup_and_foreign_names_are_ignored(self):
        self.touch("run.db.bak-20261016", "run.db-wal", "runs.db", "run..db", "run.a b.db",
                   "run." + "x" * 33 + ".db", "events.db")
        self.assertEqual({}, stores.lane_db_paths(self.root))

    def test_a_lane_name_of_32_characters_is_accepted(self):
        self.touch("run." + "x" * 32 + ".db")
        self.assertEqual(["x" * 32], list(stores.lane_db_paths(self.root)))

    def test_a_missing_state_folder_gives_no_lanes(self):
        self.assertEqual({}, stores.lane_db_paths(self.root / "elsewhere"))

    def test_declared_lanes_without_a_database_are_listed_after_the_default(self):
        self.touch("run.db", "run.fix.db", "run.db.bak-1")
        (self.root / ".agentry" / "pipeline.json").write_text(
            '{"lanes": {"_comment": "x", "default": {"repo": "a"}, "ghost": {"repo": "b"},'
            ' "bad": "not an object"}}', encoding="utf-8")
        self.assertEqual(["default", "fix", "ghost"], stores.lane_names(self.root))
        self.assertEqual({"default": "a", "ghost": "b"}, stores.declared_lanes(self.root))

    def test_dial_files_are_suffixed_for_every_lane_but_the_default(self):
        self.assertEqual(self.state / "mode", stores.dial_file(self.root, "mode", "default"))
        self.assertEqual(self.state / "mode.fix", stores.dial_file(self.root, "mode", "fix"))
        (self.state / "swarm.fix").write_text("ON\n", encoding="utf-8")
        self.assertEqual("on", stores.read_dial(self.root, "swarm", "fix"))
        self.assertEqual("", stores.read_dial(self.root, "swarm", "default"))


class QueryStringTest(unittest.TestCase):

    def test_parse_query_decodes_and_keeps_the_first_value(self):
        self.assertEqual({"a": "1", "b": "\xe9 x", "c": ""},
                         stores.parse_query("a=1&b=%C3%A9+x&a=2&c="))

    def test_percent_decode_rejects_invalid_utf8(self):
        self.assertIsNone(stores.percent_decode("%ff%fe"))
        self.assertEqual("/a b", stores.percent_decode("/a%20b"))

    def test_percent_decode_is_applied_once(self):
        self.assertEqual("%2f", stores.percent_decode("%252f"))


class ParamsTest(unittest.TestCase):

    def params(self, query: str) -> stores.Params:
        return stores.Params(stores.parse_query(query))

    def test_integer_defaults_and_range(self):
        p = self.params("limit=5&offset=0")
        self.assertEqual((5, 0), p.page(25, 500))
        p.check()
        self.assertEqual((25, 0), self.params("").page(25, 500))

    def test_every_bad_integer_is_reported_under_its_own_name(self):
        for query, field in (("limit=0", "limit"), ("limit=501", "limit"), ("limit=abc", "limit"),
                             ("limit=1.5", "limit"), ("limit=%2B5", "limit"), ("limit=1_0", "limit"),
                             ("limit=%ff", "limit"), ("limit=%ff&limit=3", "limit"),
                             ("limit=%D9%A5", "limit"), ("limit=%EF%BC%95", "limit"),
                             ("offset=-1", "offset"), ("offset=abc", "offset"),
                             ("offset=" + "9" * 30, "offset")):
            with self.subTest(query=query):
                p = self.params(query)
                p.page(25, 500)
                with self.assertRaises(stores.ApiError) as ctx:
                    p.check()
                self.assertEqual(422, ctx.exception.status)
                self.assertEqual("validation_failed", ctx.exception.code)
                self.assertIn(field, ctx.exception.errors)

    def test_all_errors_are_collected_before_the_422(self):
        p = self.params("limit=0&offset=-1&kind=zzz")
        p.page(25, 500)
        p.choice("kind", ("a", "b"))
        with self.assertRaises(stores.ApiError) as ctx:
            p.check()
        self.assertEqual({"limit", "offset", "kind"}, set(ctx.exception.errors))

    def test_blank_values_are_the_same_as_absent_ones(self):
        p = self.params("limit=&kind=&type=&include_superseded=")
        self.assertEqual((25, 0), p.page(25, 500))
        self.assertEqual("", p.choice("kind", ("a",)))
        self.assertEqual("", p.match("type", re.compile("x"), "hint"))
        self.assertFalse(p.boolean("include_superseded"))
        p.check()

    def test_choice_match_boolean_and_date(self):
        p = self.params("kind=pattern&type=a%20b&flag=yes&since=2026-02-30&day=2026-10-16")
        self.assertEqual("pattern", p.choice("kind", ("lesson", "pattern")))
        p.match("type", re.compile(r"[a-z]+"), "letters only")
        p.boolean("flag")
        p.date("since")
        self.assertEqual("2026-10-16", p.date("day"))
        self.assertEqual({"type", "flag", "since"}, set(p.errors))

    def test_path_id_requires_four_or_more_digits(self):
        self.assertEqual("task-0001", stores.path_id("task-0001"))
        self.assertEqual("task-12345", stores.path_id("task-12345"))
        for bad in ("task-1", "nope", "task-0001x", "../task-0001", ""):
            with self.subTest(bad=bad), self.assertRaises(stores.ApiError) as ctx:
                stores.path_id(bad)
            self.assertEqual(422, ctx.exception.status)


class TimeTest(unittest.TestCase):

    def test_a_day_is_midnight_utc(self):
        self.assertEqual("2026-10-16T00:00:00Z", stores.iso_z("2026-10-16"))

    def test_offsets_are_converted_to_utc(self):
        self.assertEqual("2026-10-16T08:00:00Z", stores.iso_z("2026-10-16T10:00:00+02:00"))
        self.assertEqual("2026-10-16T10:00:00Z", stores.iso_z("2026-10-16T10:00:00+00:00"))
        self.assertEqual("2026-10-16T10:00:00Z", stores.iso_z("2026-10-16T10:00:00Z"))
        self.assertEqual("2026-10-16T10:00:00Z", stores.iso_z("2026-10-16T10:00:00"))

    def test_absent_and_unreadable_values_are_empty_strings(self):
        for value in (None, "", "   ", "yesterday", "2026-13-45"):
            with self.subTest(value=value):
                self.assertEqual("", stores.iso_z(value))

    def test_epoch_seconds(self):
        self.assertEqual("1970-01-01T00:00:10Z", stores.epoch_iso(10))
        self.assertEqual("", stores.epoch_iso("junk"))


class FrontmatterTest(unittest.TestCase):

    def test_the_body_starts_after_the_closing_fence_and_keeps_crlf(self):
        head, body = stores.split_frontmatter("---\r\nid: 1\r\n---\r\nline one\r\nline two\r\n")
        self.assertEqual("id: 1", head)
        self.assertEqual("line one\r\nline two\r\n", body)

    def test_without_a_fence_the_whole_text_is_the_body(self):
        self.assertEqual(("", "# just markdown\n"), stores.split_frontmatter("# just markdown\n"))

    def test_an_empty_frontmatter_block(self):
        self.assertEqual(("", "body"), stores.split_frontmatter("---\n---\nbody"))

    def test_a_byte_order_mark_is_ignored(self):
        self.assertEqual("id: 1", stores.split_frontmatter("\ufeff---\nid: 1\n---\nx")[0])

    def test_scalars_lists_quotes_and_nested_maps(self):
        parsed = stores.parse_head(
            'name: x\ndescription: "a: b"\nsingle: \'q\'\nrules:\n  - a.md\n  - b.md\n'
            "experimental:\n  cacheTtl: 1h\ndepends_on: [task-0001, task-0002]\n# comment\nempty:")
        self.assertEqual({"name": "x", "description": "a: b", "single": "q", "rules": ["a.md", "b.md"],
                          "experimental": "", "depends_on": "[task-0001, task-0002]", "empty": ""},
                         parsed)

    def test_cap_text_cuts_on_a_character_boundary(self):
        text = "a" * 10 + "é" * 10  # 10 + 20 bytes
        for cap in range(8, 31):
            with self.subTest(cap=cap):
                cut, truncated = stores.cap_text(text, cap)
                self.assertLessEqual(len(cut.encode("utf-8")), cap)
                self.assertNotIn("�", cut)
                self.assertEqual(cap < 30, truncated)
        self.assertEqual((text, False), stores.cap_text(text, 30))


class TaskScanTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_tasks_")
        for folder in stores.FOLDERS:
            (self.root / ".agentry" / "tasks" / folder).mkdir(parents=True)
        (self.root / ".agentry" / "tasks" / "epics").mkdir()

    def put(self, folder: str, name: str, text: str) -> Path:
        path = self.root / ".agentry" / "tasks" / folder / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_only_task_files_are_listed_with_their_folder(self):
        self.put("backlog", "task-0001.md", "---\ntitle: One\ntype: feature\n---\nbody")
        self.put("done", "task-12345.md", "---\ntitle: Five digits\n---\n")
        self.put("done", "task-0002-notes.md", "---\ntitle: Not a task file\n---\n")
        self.put("done", "README.md", "# readme")
        found = {t["id"]: t for t in stores.scan_tasks(self.root)}
        self.assertEqual({"task-0001", "task-12345"}, set(found))
        one = found["task-0001"]
        self.assertEqual(("backlog", "One", "feature"), (one["folder"], one["title"], one["type"]))

    def test_dependencies_and_dates_are_normalised(self):
        self.put("active", "task-0003.md",
                 "---\ndepends_on: [task-0001, task-0002]\ncreated: 2026-10-01\ncompleted:\n"
                 "superseded_by: task-0009\numbrella: true\nsubtasks: [task-0004]\n---\n")
        task = stores.scan_tasks(self.root)[0]
        self.assertEqual(["task-0001", "task-0002"], task["depends_on"])
        self.assertEqual("2026-10-01T00:00:00Z", task["created"])
        self.assertEqual("", task["completed"])
        self.assertEqual("task-0009", task["superseded_by"])
        self.assertTrue(task["umbrella"])
        self.assertEqual(["task-0004"], task["subtasks"])

    def test_a_changed_file_is_read_again(self):
        path = self.put("backlog", "task-0001.md", "---\ntitle: Before\n---\n")
        self.assertEqual("Before", stores.scan_tasks(self.root)[0]["title"])
        path.write_bytes(b"---\ntitle: After the edit\n---\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual("After the edit", stores.scan_tasks(self.root)[0]["title"])

    def test_a_missing_folder_is_not_an_error(self):
        self.assertEqual([], stores.scan_tasks(self.root / "elsewhere"))

    def test_the_earlier_folder_wins_for_a_duplicated_id(self):
        self.put("done", "task-0001.md", "---\ntitle: Done copy\n---\n")
        self.put("active", "task-0001.md", "---\ntitle: Live copy\n---\n")
        index = stores.task_index(stores.scan_tasks(self.root))
        self.assertEqual("Live copy", index["task-0001"]["title"])
        self.assertEqual("active", stores.find_task_file(self.root, "task-0001")[0])

    def test_task_ref_of_an_unknown_task_has_empty_fields(self):
        self.assertEqual({"id": "task-0099", "title": "", "folder": ""}, stores.task_ref("task-0099", {}))

    def test_ids_sort_numerically(self):
        self.put("done", "task-9999.md", "---\n---\n")
        self.put("done", "task-10000.md", "---\n---\n")
        ordered = sorted(stores.scan_tasks(self.root), key=stores.task_sort_key)
        self.assertEqual(["task-9999", "task-10000"], [t["id"] for t in ordered])

    def test_epics_skip_thread_documents(self):
        epics = self.root / ".agentry" / "tasks" / "epics"
        (epics / "epic-0001-x.md").write_text("---\ntitle: X\nstatus: planned\ntasks: [task-0001]\n---\n",
                                              encoding="utf-8")
        (epics / "epic-0001-thread.md").write_text("---\ntitle: Thread\n---\n", encoding="utf-8")
        (epics / "notes.md").write_text("---\ntitle: Not an epic\n---\n", encoding="utf-8")
        self.assertEqual([{"id": "epic-0001", "title": "X", "status": "planned", "tasks": ["task-0001"]}],
                         stores.scan_epics(self.root))


class ReadersStayLinearTest(unittest.TestCase):
    """Text the repository controls must not stall the server (the GIL is held while a regex
    backtracks): `title: x` + 16000 spaces + `y` took seconds with a lazy `(.*?)[ \\t]*$`."""

    EVIL_VALUE = "title: x" + " " * 16000 + "y"
    EVIL_ITEM = "tools:\n  - x" + " " * 16000 + "y"
    LIMIT_SECONDS = 0.05

    def setUp(self):
        stores.ensure_tool_paths()
        import views_harness
        self.harness = views_harness
        self.base = tmproot.sandbox(self, "dash_redos_")

    def fast(self, call):
        best = min(self.elapsed(call) for _ in range(3))
        self.assertLess(best, self.LIMIT_SECONDS)

    @staticmethod
    def elapsed(call) -> float:
        start = time.perf_counter()
        call()
        return time.perf_counter() - start

    def write(self, name: str, text: str) -> Path:
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_head_keeps_the_value_and_the_list_item_trimmed(self):
        self.assertEqual({"title": "x"}, stores.parse_head("title:   x \t "))
        self.assertEqual({"tools": ["a", "b"]}, stores.parse_head("tools:\n  - a  \n  - b\t"))

    def test_parse_head_is_linear_on_long_values_and_list_items(self):
        self.fast(lambda: stores.parse_head(self.EVIL_VALUE))
        self.fast(lambda: stores.parse_head(self.EVIL_ITEM))

    def test_parse_head_ignores_everything_past_the_frontmatter_cap(self):
        head = "title: X\n" + "# pad\n" * 5000 + "late: value\n"
        self.assertEqual({"title": "X"}, stores.parse_head(head))

    def test_task_listing_is_linear(self):
        root = self.base / "project"
        folder = root / ".agentry" / "tasks" / "backlog"
        folder.mkdir(parents=True)
        (folder / "task-0001.md").write_text(f"---\n{self.EVIL_VALUE}\n---\nbody\n", encoding="utf-8")
        stores._task_cache.clear()
        self.addCleanup(stores._task_cache.clear)
        self.assertLess(self.elapsed(lambda: stores.scan_tasks(root)), self.LIMIT_SECONDS)

    def test_agent_and_skill_headers_are_read_linearly(self):
        agent = self.write("claude/agents/a.md", f"---\n{self.EVIL_VALUE}\n---\n")
        self.write("claude/skills/s/SKILL.md", f"---\n{self.EVIL_VALUE}\n---\n")
        self.fast(lambda: self.harness._front(agent))
        self.fast(lambda: self.harness._skills(self.base / "claude" / "skills", "project", "", []))

    def test_the_handoff_chain_and_a_huge_header_are_linear(self):
        directory = self.base / "handoffs"
        self.write("handoffs/task-0001.md", f"---\n{self.EVIL_VALUE}\n---\nbody\n")
        self.fast(lambda: self.harness.chain(directory))
        huge = ("---\n" + (self.EVIL_VALUE + "\n") * 2000)[:stores.READ_CAP_BYTES] + "---\nbody\n"
        self.fast(lambda: stores.parse_head(stores.split_frontmatter(huge)[0]))

    def test_roster_lines_past_the_cap_are_skipped(self):
        evil = "**" + "a" * 20000 + "\n| " + " " * 20000 + "x |\n"
        self.fast(lambda: self.harness._roster(evil))
        text = "**Technical department**\n| architect | readonly |\n"
        self.assertEqual({"architect": "Technical"}, self.harness._roster(text)[0])

    def test_department_headings_parse_in_linear_time_with_the_old_meaning(self):
        heading = self.harness._department_heading
        for line, expected in (("**Technical department [ACTIVE]:**", "Technical"),
                               ("**Content & Growth DEPARTMENT [ACTIVE]**", "Content & Growth"),
                               ("**Business department**", "Business"),
                               ("**Departments**", ""), ("**x departmental**", ""),
                               ("Technical department", ""), ("**department**", "")):
            with self.subTest(line=line):
                self.assertEqual(expected, heading(line))
        evil = "**" + "a department" * 30 + " " * 300 + "departmentx"
        self.fast(lambda: [heading(evil) for _ in range(200)])
        self.fast(lambda: self.harness._roster((evil + "\n") * 2500))

    def test_hook_command_scan_is_linear(self):
        self.fast(lambda: self.harness._script("a" * 100000, self.base, self.base))
        self.fast(lambda: self.harness._script(("a" * 299 + ".") * 300, self.base, self.base))
        command = 'python "${CLAUDE_PLUGIN_ROOT}/tools/x.py"'
        self.assertEqual(("tools/x.py", False), self.harness._script(command, self.base, self.base))

    def test_json_and_dial_files_are_read_up_to_a_cap(self):
        big = self.write("big.json", " " * (3 * stores.READ_CAP_BYTES))
        self.assertEqual({}, stores.read_json(big))
        self.fast(lambda: stores.read_json(big))
        self.write(".agentry/state/mode", "solo" + " " * 100000 + "x")
        self.assertEqual("solo", stores.read_dial(self.base, "mode", stores.DEFAULT_LANE))

    def test_declared_lane_names_are_validated(self):
        pipeline = self.base / "project" / ".agentry" / "pipeline.json"
        self.write("project/.agentry/pipeline.json", json.dumps(
            {"lanes": {"fix": {"repo": "r"}, "../evil": {"repo": "r"}, "a" * 33: {"repo": "r"},
                       "with space": {"repo": "r"}, "_note": {}}}))
        self.assertEqual({"fix": "r"}, stores.declared_lanes(pipeline.parent.parent))

    def test_the_manifest_path_is_spelled_in_one_place(self):
        self.assertEqual(Path("p") / ".claude-plugin" / "plugin.json", stores.manifest_path(Path("p")))


if __name__ == "__main__":
    unittest.main()

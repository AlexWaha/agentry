"""QA (backend) unit-level checks of the dashboard server and its four harness refactors (task-0149).

These tests were written adversarially, against the code and not against its own tests. Most pin a
behaviour that already holds (boundary-safe truncation, the date normaliser, the lane regex, the
plugin manifest path) so a later edit cannot loosen it. Four of them reproduce defects found by QA
and fail until the owning module is fixed; each says which in its docstring:

  * handoff.uncovered_done_tasks() releases busy markers after an internal error      (core)
  * ws.decode_message() lets RecursionError escape on deeply nested JSON              (realtime)
  * stores.scan_tasks() raises when a task file vanishes between listing and stat     (core)
  * stores.scan_tasks() caches a task whose file could not be read as an empty task   (core)
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import random
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import busy
import handoff
import state
import stores
import tmproot
import ws

WAIVER = "---\ntask: {task}\nwaived: true\nreason: swept with its umbrella\n---\n"


class HandoffMarkerReleaseTest(unittest.TestCase):
    """NFR-5 and NFR-6: uncovered_done_tasks() is owed() plus the marker release, and an internal
    error inside the check is fail-open WITHOUT side effects, exactly as before the refactor."""

    TASK = "task-9002"

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_qa_marker_").resolve()
        tasks = self.root / ".agentry" / "tasks"
        self.dirs = {name: tasks / name for name in ("backlog", "active", "done")}
        self.handoffs = tasks / "handoffs"
        for folder in (*self.dirs.values(), self.handoffs, self.root / ".agentry" / "state"):
            folder.mkdir(parents=True)
        for target, name, value in (
            (state, "ROOT", self.root),
            (state, "TASK_DIRS", self.dirs),
            (state, "STATE_DIR", self.root / ".agentry" / "state"),
            (state, "DB_PATH", self.root / ".agentry" / "state" / "run.db"),
        ):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = unittest.mock.patch.object(
            handoff, "cfg", side_effect=lambda: {"enabled": True, "baseline": ""}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.dirs["done"] / f"{self.TASK}.md").write_text("---\nid: 1\n---\nbody\n", encoding="utf-8")

    def write_valid_document(self) -> None:
        (self.handoffs / f"{self.TASK}.md").write_text(WAIVER.format(task=self.TASK), encoding="utf-8")

    def test_the_marker_of_a_task_whose_document_now_validates_is_released(self):
        self.write_valid_document()
        busy.acquire(self.TASK, "handoff")
        self.assertEqual([], handoff.uncovered_done_tasks())
        self.assertEqual(busy.ABSENT, busy.read(self.TASK).status)

    def test_owed_alone_never_touches_a_marker(self):
        self.write_valid_document()
        busy.acquire(self.TASK, "handoff")
        self.assertEqual([], handoff.owed())
        self.assertEqual(busy.FRESH, busy.read(self.TASK).status)

    def test_the_marker_of_a_task_still_owed_is_kept(self):
        busy.acquire(self.TASK, "handoff")
        self.assertEqual([self.TASK], [d["task"] for d in handoff.uncovered_done_tasks()])
        self.assertEqual(busy.FRESH, busy.read(self.TASK).status)

    def test_the_marker_of_a_live_gate_is_never_released(self):
        self.write_valid_document()
        busy.acquire(self.TASK, "implement")
        handoff.uncovered_done_tasks()
        self.assertEqual(busy.FRESH, busy.read(self.TASK).status)

    def test_an_internal_error_fails_open_and_leaves_every_marker_in_place(self):
        """DEFECT (core, handoff.py uncovered_done_tasks): owed() swallows the error and returns [],
        and the wrapper then calls drop_scaffold_markers(set()), which releases the marker of a
        document nobody validated. Before the refactor the error returned [] with the marker kept."""
        busy.acquire(self.TASK, "handoff")
        with unittest.mock.patch.object(handoff, "covered_required", side_effect=RuntimeError("boom")):
            self.assertEqual([], handoff.uncovered_done_tasks())
        self.assertEqual(
            busy.FRESH,
            busy.read(self.TASK).status,
            "an error while scanning released a marker whose document was never validated",
        )


class WsDecodeMessageTest(unittest.TestCase):
    """FR-42: a text message that is not valid UTF-8 JSON closes with 1007, and nothing a client
    sends may end its reader thread any other way."""

    def test_invalid_utf8_and_invalid_json_are_1007(self):
        for data in (b"\xff\xfe", b"not json", b"{", b""):
            with self.subTest(data=data), self.assertRaises(ws.ProtocolError) as caught:
                ws.decode_message(data)
            self.assertEqual(1007, caught.exception.code)

    def test_valid_json_of_any_shape_is_returned_for_the_caller_to_judge(self):
        for data, expected in ((b"{}", {}), (b"[]", []), (b"null", None), (b'"x"', "x"), (b"1", 1)):
            with self.subTest(data=data):
                self.assertEqual(expected, ws.decode_message(data))

    def test_deeply_nested_json_is_a_protocol_error_not_a_crash(self):
        """DEFECT (realtime, ws.py decode_message): json.loads raises RecursionError, which is not a
        ValueError, so it escapes _on_frame and _read_loop and kills the reader thread: the client
        stays registered, is never answered, never times out and holds one of the eight slots."""
        for nested in (b"[" * 30000 + b"]" * 30000, b'{"a":' * 12000 + b"1" + b"}" * 12000):
            with self.subTest(size=len(nested)):
                with self.assertRaises(ws.ProtocolError) as caught:
                    ws.decode_message(nested)
                self.assertEqual(1007, caught.exception.code)


class TaskScanRobustnessTest(unittest.TestCase):
    """The task folders change under a running server: advance.py moves files between them and an
    editor or a scanner may hold one for a moment."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_qa_scan_").resolve()
        self.folder = self.root / ".agentry" / "tasks" / "backlog"
        self.folder.mkdir(parents=True)
        stores._task_cache.clear()
        self.addCleanup(stores._task_cache.clear)

    def write(self, task_id: str, title: str) -> Path:
        path = self.folder / f"{task_id}.md"
        path.write_text(f"---\ntitle: {title}\ntype: feature\n---\nbody\n", encoding="utf-8")
        return path

    def test_a_listing_reads_every_file_and_parses_the_front_matter(self):
        self.write("task-0001", "First")
        self.write("task-0002", "Second")
        found = {t["id"]: (t["title"], t["folder"]) for t in stores.scan_tasks(self.root)}
        self.assertEqual({"task-0001": ("First", "backlog"), "task-0002": ("Second", "backlog")}, found)

    def test_a_file_that_vanishes_between_the_listing_and_its_stat_is_skipped(self):
        """DEFECT (core, stores.py cached_parse): DirEntry.stat() is a system call on Linux and macOS
        (on Windows it is served from the listing), so a task file moved out of the folder by
        advance.py between scandir() and stat() raises FileNotFoundError and the request is a 500."""
        self.write("task-0001", "Stays")
        self.write("task-0002", "Vanishes")
        real_scandir = stores.os.scandir

        class Vanishing:
            def __init__(self, entry):
                self.name, self.path = entry.name, entry.path

            def is_file(self):
                return True

            def stat(self):
                raise FileNotFoundError(self.path)

        def scandir(path):
            entries = list(real_scandir(path))
            return [Vanishing(e) if e.name == "task-0002.md" else e for e in entries]

        with unittest.mock.patch.object(stores.os, "scandir", scandir):
            found = [t["id"] for t in stores.scan_tasks(self.root)]
        self.assertEqual(["task-0001"], found)

    def test_a_transient_read_failure_is_not_cached_as_an_empty_task(self):
        """DEFECT (core, stores.py read_capped/cached_parse): read_capped turns every OSError into an
        empty string, the parse of "" is cached against the file's (mtime, size), and the task then
        stays blank (no title, type or epic) until the file is next modified."""
        self.write("task-0001", "Readable")
        with unittest.mock.patch.object(Path, "open", side_effect=PermissionError("sharing violation")):
            stores.scan_tasks(self.root)
        titles = [t["title"] for t in stores.scan_tasks(self.root)]
        self.assertEqual(["Readable"], titles)


class StoresHelpersTest(unittest.TestCase):
    def test_cap_text_never_exceeds_the_cap_never_splits_a_character_and_returns_a_prefix(self):
        generator = random.Random(149)
        alphabet = ["a", "\u00e9", "\u0434", "\u20ac", "\U0001f600", "\r\n", "x"]
        for _ in range(300):
            text = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 60)))
            cap = generator.randint(0, 80)
            cut, truncated = stores.cap_text(text, cap)
            raw = cut.encode("utf-8")
            with self.subTest(text=text, cap=cap):
                self.assertLessEqual(len(raw), cap)
                self.assertTrue(text.startswith(cut))
                self.assertEqual(len(text.encode("utf-8")) > cap, truncated)
                if not truncated:
                    self.assertEqual(text, cut)

    def test_the_body_cap_keeps_a_multibyte_character_whole_at_the_boundary(self):
        text = "a" * (stores.BODY_CAP_BYTES - 1) + "\u20ac" + "tail"
        cut, truncated = stores.cap_text(text)
        self.assertTrue(truncated)
        self.assertEqual(stores.BODY_CAP_BYTES - 1, len(cut.encode("utf-8")))
        self.assertNotIn("\ufffd", cut)

    def test_iso_z_normalises_dates_offsets_and_garbage(self):
        cases = {
            "2026-10-16": "2026-10-16T00:00:00Z",
            "2026-10-16T10:00:00Z": "2026-10-16T10:00:00Z",
            "2026-10-16T10:00:00": "2026-10-16T10:00:00Z",
            "2026-10-16T10:00:00+03:00": "2026-10-16T07:00:00Z",
            "2026-10-16 10:00:00": "2026-10-16T10:00:00Z",
            "2026-10-16T10:00:00.250Z": "2026-10-16T10:00:00Z",
            "2026-02-30": "",
            "yesterday": "",
            "": "",
            None: "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, stores.iso_z(raw))

    def test_the_lane_database_pattern_accepts_only_run_databases(self):
        accepted = ("run.db", "run.fix.db", "run.a-b_c.db", "run." + "x" * 32 + ".db")
        refused = (
            "run..db",
            "run.db.bak-1",
            "run.db-wal",
            "run.x y.db",
            "run." + "x" * 33 + ".db",
            "xrun.db",
            "events.db",
            "run.fix.db.bak",
        )
        for name in accepted:
            with self.subTest(accepted=name):
                self.assertIsNotNone(stores.LANE_DB_RE.match(name))
        for name in refused:
            with self.subTest(refused=name):
                self.assertIsNone(stores.LANE_DB_RE.match(name))

    def test_the_first_occurrence_of_a_query_parameter_wins(self):
        self.assertEqual({"limit": "5", "q": "a b"}, stores.parse_query("limit=5&limit=500&q=a+b"))

    def test_integer_parameters_accept_exactly_their_range(self):
        for text, ok in (
            ("1", True),
            ("500", True),
            ("0", False),
            ("501", False),
            ("-1", False),
            ("1.5", False),
            ("1e2", False),
            ("0x10", False),
            ("", True),
            ("+5", False),
        ):
            params = stores.Params({"limit": text})
            params.integer("limit", 25, 1, 500)
            with self.subTest(text=text):
                self.assertEqual(ok, not params.errors)

    def test_a_page_offset_is_bounded_so_a_slice_cannot_overflow(self):
        for text, ok in (
            ("0", True),
            (str(stores.MAX_OFFSET), True),
            (str(stores.MAX_OFFSET + 1), False),
            ("99999999999999999999", False),
        ):
            params = stores.Params({"offset": text})
            params.page(25, 500)
            with self.subTest(text=text):
                self.assertEqual(ok, not params.errors)


class PluginManifestTest(unittest.TestCase):
    def test_the_manifest_is_read_from_the_plugin_root_of_this_checkout(self):
        plugin_root = TOOLS_DIR.parent
        on_disk = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, stores.plugin_manifest(plugin_root))

    def test_a_folder_without_a_manifest_is_an_empty_manifest(self):
        self.assertEqual({}, stores.plugin_manifest(tmproot.sandbox(self, "dash_qa_manifest_")))


if __name__ == "__main__":
    unittest.main()

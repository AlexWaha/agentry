"""task-0060 S2: zone declaration, disjointness proof, freeze and lookup."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import state
import tmproot
import zones

TASK = "task-0120"

BOARD = """# board

## Zones

| zone | paths |
|---|---|
| 0060-S2 | `plugin/tools/pipeline/zones.py`, `docs/zones/**` |
| 0101 | plugin/tools/pipeline/supervisor.py |

## task-0060

notes
"""


class PathFormTest(unittest.TestCase):
    def test_exact_paths_and_dir_globs_are_accepted_and_normalised(self):
        cases = {"a/b.py": (False, "a/b.py"), "`a\\b.py`": (False, "a/b.py"),
                 "./docs/**": (True, "docs/**"), "src/pkg/**": (True, "src/pkg/**")}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(zones.parse_path(raw), expected)

    def test_every_other_form_is_rejected(self):
        for raw in ("**", "/**", "docs/", "src/*.py", "src/**/x.py", "a/?.md", "a/[b].md",
                    "/etc/passwd", "C:/x.py", "../x", "a/../b", "a//b", "a b.md", "a/**/",
                    "a:stream", ""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                zones.parse_path(raw)


class DisjointnessTest(unittest.TestCase):
    def test_overlap_through_a_glob_only_names_the_file(self):
        problems = zones.check({"A": ["docs/**"], "B": ["docs/a.md"]}, [])
        self.assertEqual(problems, ["zones A and B overlap on docs/a.md"])

    def test_exact_duplicate_is_an_overlap(self):
        problems = zones.check({"A": ["src/x.py"], "B": ["SRC/x.py"]}, [])
        self.assertEqual(problems, ["zones A and B overlap on src/x.py"])

    def test_nested_globs_overlap_and_name_the_narrower_one(self):
        problems = zones.check({"A": ["src/**"], "B": ["src/pkg/**"]}, [])
        self.assertEqual(problems, ["zones A and B overlap on src/pkg/**"])

    def test_a_glob_does_not_cover_a_sibling_directory_with_the_same_prefix(self):
        self.assertEqual(zones.check({"A": ["src/**"], "B": ["src2/x.py", "src2/**"]}, []), [])

    def test_a_glob_does_not_cover_the_directory_name_itself(self):
        self.assertEqual(zones.check({"A": ["docs/**"], "B": ["docs"]}, []), [])

    def test_clean_pair_passes(self):
        self.assertEqual(zones.check({"A": ["docs/**", "README.md"], "B": ["src/a.py"]},
                                     [".claude/settings.json"]), [])

    def test_shared_file_covered_only_by_a_glob_is_refused(self):
        problems = zones.check({"A": [".claude/**"], "B": ["src/a.py"]},
                               [".claude/settings.json"])
        self.assertEqual(len(problems), 1)
        self.assertIn("shared file .claude/settings.json", problems[0])
        self.assertIn(".claude/**", problems[0])
        self.assertIn("zone A", problems[0])

    def test_shared_file_named_exactly_in_the_covering_zone_is_allowed(self):
        self.assertEqual(zones.check({"A": [".claude/**", ".claude/settings.json"]},
                                     [".claude/settings.json"]), [])

    def test_shared_file_named_in_one_zone_and_globbed_by_another_is_an_overlap(self):
        problems = zones.check({"A": [".claude/settings.json"], "B": [".claude/**"]},
                               [".claude/settings.json"])
        self.assertEqual(problems, ["zones A and B overlap on .claude/settings.json"])

    def test_shared_directory_overlapping_a_zone_glob_is_refused(self):
        problems = zones.check({"A": [".agentry/**"]}, [".agentry/tasks/**"])
        self.assertEqual(len(problems), 1)
        self.assertIn("shared directory .agentry/tasks/**", problems[0])

    def test_shared_directory_with_exact_zone_files_inside_is_allowed(self):
        self.assertEqual(zones.check({"A": [".agentry/tasks/swarm/t/board.md"]},
                                     [".agentry/tasks/**"]), [])


class BoardParseTest(unittest.TestCase):
    def test_table_is_read_between_the_zones_heading_and_the_next_section(self):
        parsed, errors = zones.parse_board(BOARD)
        self.assertEqual(errors, [])
        self.assertEqual(parsed, {
            "0060-S2": ["plugin/tools/pipeline/zones.py", "docs/zones/**"],
            "0101": ["plugin/tools/pipeline/supervisor.py"]})

    def test_bad_rows_are_reported_with_the_offending_text(self):
        board = ("## Zones\n| zone | paths |\n|---|---|\n| A | src/*.py |\n| A | b.py |\n"
                 "| bad id! | c.py |\n| E | |\n| x | y | z |\n")
        parsed, errors = zones.parse_board(board)
        text = "\n".join(errors)
        self.assertIn("zone A: 'src/*.py'", text)
        self.assertIn("zone A is declared twice", text)
        self.assertIn("'bad id!'", text)
        self.assertIn("zone E declares no paths", text)
        self.assertIn("exactly two cells", text)
        self.assertNotIn("0060", text)
        self.assertEqual(parsed["A"], [])

    def test_missing_section_and_empty_table_are_errors(self):
        self.assertEqual(zones.parse_board("# board\n")[1], ["board has no '## Zones' section"])
        self.assertEqual(zones.parse_board("## Zones\n| zone | paths |\n|--|--|\n")[1],
                         ["the '## Zones' table has no rows"])


class TranscriptZoneTest(unittest.TestCase):
    def setUp(self):
        self.dir = tmproot.sandbox(self, "zones_tr_")
        self.sub = self.dir / "session-1" / "subagents"
        self.sub.mkdir(parents=True)

    def write(self, agent_id, content, role="user"):
        line = {"type": "user", "message": {"role": role, "content": content}}
        (self.sub / f"agent-{agent_id}.jsonl").write_text(
            json.dumps(line) + "\n" + json.dumps({"type": "assistant"}) + "\n", encoding="utf-8")

    def test_own_transcript_zone_wins_over_a_parent_naming_other_zones(self):
        parent = self.dir / "session-1.jsonl"
        parent.write_text("\n".join(json.dumps({"message": {"role": "user", "content": c}})
                                    for c in ("Swarm zone: 0101\nx", "Swarm zone: FX-1\ny",
                                              "Swarm zone: 0060-S2\nz")), encoding="utf-8")
        self.write("a3d9c94df3116b37f", "Swarm zone: 0060-S2\nTask: build zones.py")
        self.write("b111", "Swarm zone: 0101\nTask: other")
        self.assertEqual(zones.zone_for_agent("a3d9c94df3116b37f", self.sub), "0060-S2")
        self.assertEqual(zones.zone_for_agent("b111", self.sub), "0101")

    def test_text_block_content_is_read(self):
        self.write("a1", [{"type": "text", "text": "intro\nSwarm zone: FX-2\nmore"}])
        self.assertEqual(zones.zone_for_agent("a1", self.sub), "FX-2")

    def test_no_zone_line_two_zone_lines_or_a_quoted_line_is_none(self):
        self.write("a1", "Task only")
        self.write("a2", "Swarm zone: A\nSwarm zone: B")
        self.write("a3", "> Swarm zone: A")
        self.write("a4", "Swarm zone: A\nSwarm zone: A")
        self.assertIsNone(zones.zone_for_agent("a1", self.sub))
        self.assertIsNone(zones.zone_for_agent("a2", self.sub))
        self.assertIsNone(zones.zone_for_agent("a3", self.sub))
        self.assertEqual(zones.zone_for_agent("a4", self.sub), "A")

    def test_missing_unreadable_or_non_user_first_line_is_none(self):
        self.assertIsNone(zones.zone_for_agent("nope", self.sub))
        (self.sub / "agent-bad.jsonl").write_text("not json\n", encoding="utf-8")
        self.assertIsNone(zones.zone_for_agent("bad", self.sub))
        self.write("asst", "Swarm zone: A", role="assistant")
        self.assertIsNone(zones.zone_for_agent("asst", self.sub))

    def test_agent_id_cannot_walk_out_of_the_directory(self):
        (self.dir / "agent-x.jsonl").write_text(
            json.dumps({"message": {"role": "user", "content": "Swarm zone: A"}}), encoding="utf-8")
        for agent_id in ("../agent-x", "..\\x", "", None, "a/b"):
            with self.subTest(agent_id=agent_id):
                self.assertIsNone(zones.zone_for_agent(agent_id, self.sub))


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "zones_fr_")
        self.config: dict = {}
        for patcher in (
            mock.patch.object(state, "ROOT", self.root),
            mock.patch.object(state, "STATE_DIR", self.root / ".agentry" / "state"),
            mock.patch.object(state, "DB_PATH", self.root / ".agentry" / "state" / "run.db"),
            mock.patch.object(state, "load_pipeline", lambda: self.config),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.register(TASK, "implement")
        self.board(BOARD)

    def register(self, task, stage):
        conn = state.connect()
        try:
            if state.get_run(conn, task):
                state.set_fields(conn, task, stage=stage)
            else:
                state.create_run(conn, task, "feature", stage)
            conn.commit()
        finally:
            conn.close()

    def board(self, text):
        path = zones.board_path(TASK)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_swarm_dir_sits_under_the_workspace_root_next_to_the_board(self):
        self.assertEqual(self.root / ".agentry" / "tasks" / "swarm" / TASK, zones.swarm_dir(TASK))
        self.assertEqual(zones.swarm_dir(TASK) / "board.md", zones.board_path(TASK))

    def test_swarm_dir_refuses_anything_that_is_not_a_task_id(self):
        for task in ("..", "../..", "task-0120/..", "task-0120/x", "swarm", "", None, 7):
            with self.subTest(task=task):
                self.assertIsNone(zones.swarm_dir(task))

    def test_prove_freezes_the_map_and_owner_of_resolves_paths(self):
        ok, lines = zones.prove(TASK)
        self.assertTrue(ok, lines)
        frozen = json.loads(zones.zones_path().read_text(encoding="utf-8"))
        self.assertEqual(frozen["task"], TASK)
        self.assertEqual(frozen["zones"]["0101"], ["plugin/tools/pipeline/supervisor.py"])
        self.assertIn(".claude/settings.json", frozen["shared"])
        owner = zones.owner_of
        self.assertEqual(owner("plugin/tools/pipeline/zones.py"), "0060-S2")
        self.assertEqual(owner("docs\\zones\\deep\\a.md"), "0060-S2")
        self.assertEqual(owner("./DOCS/zones/a.md"), "0060-S2")
        self.assertEqual(owner(self.root / "plugin/tools/pipeline/supervisor.py"), "0101")
        self.assertIsNone(owner("docs/zones"))
        self.assertIsNone(owner("docs/other.md"))
        self.assertIsNone(owner(".claude/settings.json"))
        self.assertIsNone(owner("docs/zones/../../x"))
        self.assertIsNone(owner("E:/elsewhere/docs/zones/a.md"))

    def test_overlap_is_refused_with_the_paths_named_and_nothing_is_frozen(self):
        self.board(BOARD.replace("| 0101 | plugin/tools/pipeline/supervisor.py |",
                                 "| 0101 | docs/zones/a.md, src/** |"))
        ok, lines = zones.prove(TASK)
        self.assertFalse(ok)
        self.assertEqual(lines, ["zones 0060-S2 and 0101 overlap on docs/zones/a.md"])
        self.assertFalse(zones.zones_path().exists())

    def test_shared_file_under_a_glob_is_refused_by_the_default_set(self):
        self.board("## Zones\n| zone | paths |\n|--|--|\n| A | .claude/** |\n| B | docs/a.md |\n")
        ok, lines = zones.prove(TASK)
        self.assertFalse(ok)
        self.assertTrue(any("shared file .claude/settings.json" in line for line in lines), lines)

    def test_configured_shared_paths_replace_the_defaults(self):
        self.config = {"swarm": {"shared_paths": ["docs/index.md"]}}
        self.board("## Zones\n| zone | paths |\n|--|--|\n| A | docs/** |\n| B | .claude/** |\n")
        ok, lines = zones.prove(TASK)
        self.assertFalse(ok)
        self.assertEqual(len(lines), 1)
        self.assertIn("shared file docs/index.md", lines[0])

    def test_a_malformed_configured_shared_path_refuses_the_proof(self):
        self.config = {"swarm": {"shared_paths": ["docs/*.md"]}}
        ok, lines = zones.prove(TASK)
        self.assertFalse(ok)
        self.assertIn("swarm.shared_paths", lines[0])

    def test_prove_refuses_outside_implement_or_test_and_for_unknown_tasks(self):
        self.register(TASK, "review")
        ok, lines = zones.prove(TASK)
        self.assertFalse(ok)
        self.assertIn("task-0120 is at review", lines[0])
        self.assertFalse(zones.prove("task-0999")[0])
        self.assertFalse(zones.prove("../task-0120")[0])

    def test_the_frozen_map_stops_applying_once_the_task_leaves_implement_or_test(self):
        zones.prove(TASK)
        for stage, live in (("implement", True), ("test", True), ("review", False),
                            ("ready", False), ("done", False)):
            with self.subTest(stage=stage):
                self.register(TASK, stage)
                self.assertEqual(zones.owner_of("docs/zones/a.md"),
                                 "0060-S2" if live else None)
                self.assertEqual(zones.load() is not None, live)

    def test_a_missing_or_corrupt_zones_file_gives_no_owner(self):
        self.assertIsNone(zones.load())
        zones.zones_path().parent.mkdir(parents=True, exist_ok=True)
        for junk in ("", "{", "[]", '{"task": 5, "zones": {}}', '{"task": "task-0120"}'):
            with self.subTest(junk=junk):
                zones.zones_path().write_text(junk, encoding="utf-8")
                self.assertIsNone(zones.load())
                self.assertIsNone(zones.owner_of("docs/zones/a.md"))

    def test_cli_exit_codes_and_show(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(zones.main(["--prove", "--task", TASK]), 0)
            self.assertEqual(zones.main(["--show"]), 0)
        self.assertIn("froze 2 zone(s)", out.getvalue())
        self.assertIn("task-0120 at implement: in force", out.getvalue())
        self.assertIn("0101: plugin/tools/pipeline/supervisor.py", out.getvalue())
        self.board("## Zones\n| zone | paths |\n|--|--|\n| A | a.py |\n| B | a.py |\n")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(zones.main(["--prove", "--task", TASK]), 1)
        self.assertIn("refused: zones A and B overlap on a.py", err.getvalue())


if __name__ == "__main__":
    unittest.main()

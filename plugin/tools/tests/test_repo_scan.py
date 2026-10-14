"""task-0034, FR-60 and FR-61: repository autodetect and the repos{} / lanes{} write.

Every test builds its own workspace and its own copy of a config in a project
sandbox. Nothing here opens the real `.agentry/pipeline.json` for writing.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "setup"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import git_state
import repo_scan
import stack_gate
import tmproot

REAL_CONFIG = Path(__file__).resolve().parents[3] / ".agentry" / "pipeline.json"
SCRIPT = TOOLS_DIR / "setup" / "repo_scan.py"

# Deliberately awkward: a tab indent, a one-line repos, a multi-line lanes with a
# comment, odd spacing around colons, no newline at the end of the file.
AWKWARD = (
    '{\n'
    '\t"_comment": "keep me",\n'
    '\t"cwd"  :  ".",\n'
    '\t"retry_budget": 3,\n'
    '\t"repos": {"old": {"path": ".", "stack": "python", "stages": {"review": ["ruff check ."]}}},\n'
    '\t"main_branch"   : "main",\n'
    '\t"lanes": {\n'
    '\t\t"_comment": "lane comment",\n'
    '\t\t"default": {"repo": "old"}\n'
    '\t},\n'
    '\t"tail": [1,2,   3]\n'
    '}'
)


ONE = [{"key": "agentry", "path": ".", "stack": "python"}]
TWO = [{"key": "api", "path": "api", "stack": "php"},
       {"key": "web", "path": "web", "stack": "node"}]


def fake_repo(directory: Path, *manifests: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".git").mkdir()
    for name in manifests:
        (directory / name).write_text("", encoding="utf-8")
    return directory


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_scan_").resolve()

    def test_two_repos_with_different_manifests_are_listed_with_their_stacks(self):
        fake_repo(self.root / "api", "composer.json")
        fake_repo(self.root / "web", "package.json", "package-lock.json")
        rows = repo_scan.scan(self.root)
        self.assertEqual(
            [("api", "api", "php", ["composer.json"]), ("web", "web", "node", ["package.json"])],
            [(r["key"], r["path"], r["stack"], r["manifests"]) for r in rows])

    def test_a_git_directory_without_a_manifest_is_listed_as_unknown_not_omitted(self):
        fake_repo(self.root / "infra")
        fake_repo(self.root / "api", "go.mod")
        rows = {r["key"]: r for r in repo_scan.scan(self.root)}
        self.assertEqual("unknown", rows["infra"]["stack"])
        self.assertEqual([], rows["infra"]["manifests"])
        self.assertEqual("go", rows["api"]["stack"])

    def test_a_single_repo_workspace_is_a_one_row_table_with_path_dot(self):
        fake_repo(self.root, "pyproject.toml")
        (self.root / "docs").mkdir()
        rows = repo_scan.scan(self.root)
        self.assertEqual(1, len(rows))
        self.assertEqual(".", rows[0]["path"])
        self.assertEqual("python", rows[0]["stack"])
        self.assertEqual(repo_scan._key_for(self.root), rows[0]["key"])

    def test_the_workspace_root_and_its_nested_repos_are_all_listed(self):
        fake_repo(self.root, "pyproject.toml")
        fake_repo(self.root / "services" / "billing", "go.mod")
        self.assertEqual([".", "services/billing"], [r["path"] for r in repo_scan.scan(self.root)])

    def test_the_walk_stops_two_levels_down_and_skips_vendor_and_dot_directories(self):
        fake_repo(self.root / "a" / "b" / "c", "go.mod")
        fake_repo(self.root / "node_modules" / "left-pad", "package.json")
        fake_repo(self.root / ".hidden", "package.json")
        fake_repo(self.root / "a" / "keep", "Cargo.toml")
        self.assertEqual(["a/keep"], [r["path"] for r in repo_scan.scan(self.root)])

    def test_stack_comes_from_the_first_manifest_and_every_manifest_is_shown(self):
        fake_repo(self.root / "mixed", "package.json", "composer.json", "app.csproj")
        row = repo_scan.scan(self.root)[0]
        self.assertEqual("node", row["stack"])
        self.assertEqual(["package.json", "composer.json", "app.csproj"], row["manifests"])

    def test_two_directories_with_the_same_name_get_distinct_keys(self):
        fake_repo(self.root / "a" / "app", "go.mod")
        fake_repo(self.root / "b" / "app", "go.mod")
        self.assertEqual(["app", "app-2"], [r["key"] for r in repo_scan.scan(self.root)])

    def test_an_unreadable_directory_is_reported_and_skipped_not_fatal(self):
        fake_repo(self.root / "api", "go.mod")
        fake_repo(self.root / "locked", "go.mod")
        real = repo_scan._names

        def names(directory):
            if directory.name == "locked":
                raise PermissionError("denied")
            return real(directory)

        stderr = io.StringIO()
        with unittest.mock.patch.object(repo_scan, "_names", names), contextlib.redirect_stderr(stderr):
            rows = repo_scan.scan(self.root)
        self.assertEqual(["api"], [r["key"] for r in rows])
        self.assertIn("skipped", stderr.getvalue())
        self.assertIn("locked", stderr.getvalue())

    def test_an_empty_workspace_scans_to_nothing(self):
        self.assertEqual([], repo_scan.scan(self.root))

    def test_the_rendered_table_has_one_line_per_repo_and_a_header(self):
        fake_repo(self.root / "api", "composer.json")
        fake_repo(self.root / "infra")
        lines = repo_scan.render(repo_scan.scan(self.root)).splitlines()
        self.assertEqual(3, len(lines))
        self.assertTrue(lines[0].startswith("key"))
        self.assertIn("composer.json", lines[1])
        self.assertIn("unknown", lines[2])

    def test_the_cli_lists_two_real_git_repositories(self):
        for name, manifest in (("api", "go.mod"), ("web", "package.json")):
            (self.root / name).mkdir()
            subprocess.run(["git", "init", "-q", str(self.root / name)], check=True)
            (self.root / name / manifest).write_text("", encoding="utf-8")
        done = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "--json"],
                              capture_output=True, text=True, check=True)
        rows = json.loads(done.stdout)
        self.assertEqual({"api": "go", "web": "node"}, {r["key"]: r["stack"] for r in rows})
        self.assertEqual({"key", "path", "stack", "manifests"}, set(rows[0]))


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_write_").resolve()
        self.config = self.root / "pipeline.json"

    def put(self, text: str, newline: str = "\n") -> str:
        text = text.replace("\n", newline)
        self.config.write_bytes(text.encode("utf-8"))
        return text

    def disk(self) -> str:
        return self.config.read_bytes().decode("utf-8")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (unittest.mock.patch.object(sys, "argv", ["repo_scan.py", *args]),
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            code = repo_scan.main()
        return code, out.getvalue(), err.getvalue()

    def table(self, rows) -> str:
        path = self.root / "table.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return str(path)

    # --- R7: layout survives ------------------------------------------------

    def test_only_the_repos_and_lanes_values_change_and_every_other_byte_survives(self):
        before = self.put(AWKWARD)
        spans = repo_scan.top_level_spans(before)
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        self.assertEqual(0, code)
        after = self.disk()
        new = repo_scan.top_level_spans(after)
        for key in ("repos", "lanes"):
            self.assertNotEqual(before[spans[key][1]:spans[key][2]], after[new[key][1]:new[key][2]])
        # Everything between and around the two replaced values is byte-identical.
        self.assertEqual(before[:spans["repos"][1]], after[:new["repos"][1]])
        self.assertEqual(before[spans["repos"][2]:spans["lanes"][1]],
                         after[new["repos"][2]:new["lanes"][1]])
        self.assertEqual(before[spans["lanes"][2]:], after[new["lanes"][2]:])
        self.assertEqual(list(json.loads(before)), list(json.loads(after)))

    def test_a_one_line_value_stays_on_one_line_and_a_multi_line_one_stays_indented(self):
        self.put(AWKWARD)
        self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        lines = self.disk().split("\n")
        repos_line = next(line for line in lines if line.startswith('\t"repos"'))
        self.assertIn('"web"', repos_line)
        self.assertTrue(any(line.startswith('\t\t"default"') for line in lines))

    def test_crlf_line_endings_are_preserved(self):
        self.put(AWKWARD, "\r\n")
        self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        after = self.disk()
        self.assertGreater(after.count("\r\n"), 0)
        self.assertEqual(after.count("\n"), after.count("\r\n"))

    def test_missing_keys_are_inserted_after_cwd_with_the_file_s_indent_and_endings(self):
        before = self.put('{\n    "cwd": ".",\n    "retry_budget": 3\n}\n', "\r\n")
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(ONE))
        self.assertEqual(0, code)
        after = self.disk()
        self.assertEqual(["cwd", "repos", "lanes", "retry_budget"], list(json.loads(after)))
        self.assertTrue(after.startswith('{\r\n    "cwd": ".",\r\n    "repos": {'))
        self.assertEqual(after.count("\n"), after.count("\r\n"))
        self.assertTrue(after.endswith('"retry_budget": 3\r\n}\r\n'))
        self.assertEqual(json.loads(before)["retry_budget"], json.loads(after)["retry_budget"])

    def test_the_real_config_round_trips_with_every_other_key_unchanged(self):
        if not REAL_CONFIG.is_file():
            self.skipTest("no pipeline.json in this checkout")
        before = self.put(REAL_CONFIG.read_bytes().decode("utf-8"))
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        self.assertEqual(0, code)
        old, new = json.loads(before), json.loads(self.disk())
        self.assertEqual(list(old), [k for k in new if k in old])
        for key in old:
            if key not in ("repos", "lanes"):
                self.assertEqual(old[key], new[key], key)

    def test_writing_the_same_table_twice_changes_nothing_the_second_time(self):
        self.put(AWKWARD)
        table = self.table(TWO)
        self.run_cli("--write", str(self.config), "--table", table)
        first = self.disk()
        self.run_cli("--write", str(self.config), "--table", table)
        self.assertEqual(first, self.disk())

    # --- FR-61: what lands in the config --------------------------------------

    def test_the_ceo_correction_is_what_lands_not_the_detection(self):
        workspace = self.root / "ws"
        fake_repo(workspace / "api", "go.mod")
        self.put(AWKWARD)
        detected = repo_scan.scan(workspace)
        corrected = [{**detected[0], "key": "backend", "stack": "golang", "lane": "backend-lane"}]
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(corrected))
        self.assertEqual(0, code)
        written = json.loads(self.disk())
        self.assertEqual("golang", written["repos"]["backend"]["stack"])
        self.assertNotIn("api", written["repos"])
        self.assertEqual({"repo": "backend"}, written["lanes"]["backend-lane"])

    def test_the_written_config_is_read_back_by_the_pipeline_readers(self):
        self.put(AWKWARD)
        self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        entries, lanes = repo_scan.read_back(self.config)
        self.assertEqual(["api", "web"], list(entries))
        self.assertEqual({"path": "web", "stack": "node", "stages": {}}, entries["web"])
        self.assertEqual({"default": "api", "web": "web"}, lanes)

    def test_a_single_repo_workspace_writes_one_valid_entry_and_the_default_lane(self):
        self.put('{"cwd": "."}')
        workspace = self.root / "solo"
        fake_repo(workspace, "pyproject.toml")
        code, out, _ = self.run_cli("--root", str(workspace), "--write", str(self.config))
        self.assertEqual(0, code)
        written = json.loads(self.disk())
        self.assertEqual(1, len(written["repos"]))
        (key, entry), = written["repos"].items()
        self.assertEqual(".", entry["path"])
        self.assertEqual("python", entry["stack"])
        self.assertEqual({"default": {"repo": key}}, written["lanes"])
        self.assertIn(key, out)

    def test_two_repos_get_the_default_lane_for_the_first_and_a_lane_per_key_after(self):
        self.put('{"cwd": "."}')
        self.run_cli("--write", str(self.config), "--table", self.table(TWO))
        self.assertEqual({"default": {"repo": "api"}, "web": {"repo": "web"}},
                         json.loads(self.disk())["lanes"])

    def test_existing_gate_commands_and_comment_keys_are_carried_over(self):
        self.put(AWKWARD)
        rows = [{"key": "old", "path": ".", "stack": "python"}]
        self.run_cli("--write", str(self.config), "--table", self.table(rows))
        written = json.loads(self.disk())
        self.assertEqual({"review": ["ruff check ."]}, written["repos"]["old"]["stages"])
        self.assertEqual("lane comment", written["lanes"]["_comment"])

    def test_stages_given_in_the_table_win_over_the_existing_ones(self):
        self.put(AWKWARD)
        rows = [{"key": "old", "path": ".", "stack": "python", "stages": {"test": ["pytest"]}}]
        self.run_cli("--write", str(self.config), "--table", self.table(rows))
        self.assertEqual({"test": ["pytest"]}, json.loads(self.disk())["repos"]["old"]["stages"])

    # --- refusals leave the file alone -------------------------------------------

    def test_a_path_the_readers_reject_restores_the_file_byte_for_byte(self):
        before = self.put(AWKWARD)
        rows = [{"key": "escape", "path": "../elsewhere", "stack": "go"}]
        code, _, err = self.run_cli("--write", str(self.config), "--table", self.table(rows))
        self.assertEqual(2, code)
        self.assertEqual(before, self.disk())
        self.assertIn("unchanged", err)

    def test_two_repos_on_one_lane_are_refused_and_the_file_is_unchanged(self):
        before = self.put(AWKWARD)
        rows = [{**TWO[0], "lane": "shared"}, {**TWO[1], "lane": "shared"}]
        code, _, err = self.run_cli("--write", str(self.config), "--table", self.table(rows))
        self.assertEqual(2, code)
        self.assertEqual(before, self.disk())
        self.assertIn("shared", err)

    def test_a_repeated_key_is_refused(self):
        before = self.put(AWKWARD)
        rows = [TWO[0], {**TWO[1], "key": "api"}]
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(rows))
        self.assertEqual(2, code)
        self.assertEqual(before, self.disk())

    def test_an_empty_table_is_refused_rather_than_writing_an_empty_repos(self):
        before = self.put(AWKWARD)
        code, _, err = self.run_cli("--root", str(self.root / "nothing-here"), "--write", str(self.config))
        self.assertEqual(2, code)
        self.assertEqual(before, self.disk())
        self.assertIn("no repositories", err)

    def test_a_missing_or_malformed_config_is_refused_and_never_created(self):
        code, _, err = self.run_cli("--write", str(self.config), "--table", self.table(ONE))
        self.assertEqual(2, code)
        self.assertFalse(self.config.exists())
        self.assertIn("does not exist", err)
        before = self.put("[1, 2]")
        code, _, _ = self.run_cli("--write", str(self.config), "--table", self.table(ONE))
        self.assertEqual(2, code)
        self.assertEqual(before, self.disk())

    def test_a_table_that_is_not_a_list_is_refused(self):
        self.put(AWKWARD)
        path = self.root / "bad.json"
        path.write_text('{"key": "x"}', encoding="utf-8")
        code, _, _ = self.run_cli("--write", str(self.config), "--table", str(path))
        self.assertEqual(2, code)

    def test_no_write_flag_never_touches_a_config(self):
        before = self.put(AWKWARD)
        workspace = self.root / "ws"
        fake_repo(workspace / "api", "go.mod")
        code, out, _ = self.run_cli("--root", str(workspace))
        self.assertEqual(0, code)
        self.assertIn("go", out)
        self.assertEqual(before, self.disk())


class AliasTest(unittest.TestCase):
    def test_stack_gate_resolves_a_repo_value_with_the_git_state_function(self):
        self.assertIs(git_state.resolve_key, stack_gate.resolve_key)


if __name__ == "__main__":
    unittest.main()

"""pretool_gate and agent_gate answer the same path question the same way (task-0096).

task-0075 normalised `..` and added the root CLAUDE.md in agent_gate only, so the
two gates disagreed: a docs agent's own files were frozen out during `review`,
`docs/../src/app/main.py` passed the review freeze through is_bookkeeping, the
orchestrator README pattern was a prefix match, and the edit hooks never matched
MultiEdit / NotebookEdit at all.

Each test names the one check it pins. `normalize`, `README_RE` and
`is_root_claude_md` have ONE definition (pretool_gate); agent_gate re-exports it.
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
PIPELINE_DIR = TOOLS_DIR / "pipeline"
HOOKS_JSON = TOOLS_DIR.parents[0] / "hooks" / "hooks.json"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
import state
import tmproot  # noqa: F401  (arms the in-project temp redirect)

EM_DASH = chr(0x2014)
EDIT_TOOL_NAMES = ("Edit", "Write", "MultiEdit", "NotebookEdit")
ROOT_CLAUDE_MD = str(state.ROOT / "CLAUDE.md")
TRAVERSAL = "docs/../src/app/main.py"
REVIEW_RUN = {"task": "task-0900", "stage": "review"}


def quiet(func, *args, **kwargs):
    """Call a gate and return (exit code, stderr); a deny writes its reason there."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = func(*args, **kwargs)
    return code, err.getvalue()


class SingleDefinitionTest(unittest.TestCase):
    def test_agent_gate_uses_the_pretool_gate_normalize(self):
        self.assertIs(agent_gate.normalize, pretool_gate.normalize)

    def test_agent_gate_uses_the_pretool_gate_readme_pattern(self):
        self.assertIs(agent_gate.README_RE, pretool_gate.README_RE)

    def test_agent_gate_uses_the_pretool_gate_root_claude_md_check(self):
        self.assertIs(agent_gate.is_root_claude_md, pretool_gate.is_root_claude_md)

    def test_normalize_resolves_dotdot_case_and_backslashes(self):
        cases = {
            "docs/../src/app/main.py": "src/app/main.py",
            r"Docs\..\SRC\App\Main.py": "src/app/main.py",
            ".agentry/tasks/../state/mode": ".agentry/state/mode",
            "./docs/a.md": "docs/a.md",
            "docs/a/../../b.md": "b.md",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, pretool_gate.normalize(raw))


class ReadmePatternTest(unittest.TestCase):
    README_FILES = ("README", "README.md", "README.rst", "README.txt", "readme.MD",
                    "Readme.Txt", "docs/README.md", "src/app/readme.rst",
                    r"sub\README.TXT")
    NOT_README_FILES = ("readme_gen.py", "src/app/readme_config.py", "README.py",
                        "readme.mdx", "readmes.md", "README.md.bak", "readme-notes.md",
                        "xreadme.md")

    def test_the_pattern_matches_only_readme_files(self):
        for path in self.README_FILES:
            with self.subTest(path=path, expect=True):
                self.assertTrue(pretool_gate.README_RE.search(path), path)
        for path in self.NOT_README_FILES:
            with self.subTest(path=path, expect=False):
                self.assertFalse(pretool_gate.README_RE.search(path), path)

    def test_the_orchestrator_is_denied_readme_lookalikes(self):
        for path in ("readme_gen.py", "src/app/readme_config.py"):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.orch_allowed_path(path))

    def test_the_orchestrator_may_still_write_readme_files(self):
        for path in self.README_FILES:
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.orch_allowed_path(path))

    def test_the_docs_profile_accepts_the_same_files(self):
        for path in self.README_FILES:
            with self.subTest(path=path):
                self.assertTrue(agent_gate.is_docs_path(path))
        for path in self.NOT_README_FILES:
            with self.subTest(path=path):
                self.assertFalse(agent_gate.is_docs_path(path))


class TraversalTest(unittest.TestCase):
    TRAVERSALS = (TRAVERSAL, r"docs\..\src\app\main.py", ".claude/../src/app/main.py",
                  ".agentry/tasks/../../src/app/main.py", "src/../src/app/main.py")

    def test_orch_allowed_path_judges_the_resolved_path(self):
        for path in self.TRAVERSALS:
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.orch_allowed_path(path))

    def test_is_bookkeeping_judges_the_resolved_path(self):
        for path in self.TRAVERSALS:
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_bookkeeping(path))

    def test_is_docs_path_judges_the_resolved_path(self):
        for path in self.TRAVERSALS:
            with self.subTest(path=path):
                self.assertFalse(agent_gate.is_docs_path(path))

    def test_a_traversal_that_lands_inside_a_doc_tree_is_still_allowed(self):
        for path in ("src/../docs/x.md", "app/../.claude/rules/a.md",
                     "src/../.agentry/tasks/backlog/task-0001.md"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.orch_allowed_path(path))
                self.assertTrue(pretool_gate.is_bookkeeping(path))
                self.assertTrue(agent_gate.is_docs_path(path))


class BookkeepingAcceptsDocsFilesTest(unittest.TestCase):
    def test_root_claude_md_is_bookkeeping_in_every_spelling(self):
        for path in (ROOT_CLAUDE_MD, ROOT_CLAUDE_MD.replace("\\", "/"), "CLAUDE.md",
                     "claude.md"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.is_bookkeeping(path))

    def test_a_claude_md_below_the_root_is_not_bookkeeping(self):
        for path in (str(state.ROOT / "src" / "app" / "CLAUDE.md"), "app/CLAUDE.md",
                     str(state.ROOT / "CLAUDE.md.py")):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_bookkeeping(path))

    def test_readme_files_are_bookkeeping_and_lookalikes_are_not(self):
        for path in ("README.md", "README", "docs/README.txt", "pkg/readme.rst"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.is_bookkeeping(path))
        for path in ("readme_gen.py", "src/app/readme_config.py"):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_bookkeeping(path))

    def test_the_existing_trees_stay_bookkeeping(self):
        for path in (".claude/rules/a.md", ".agentry/tasks/backlog/task-1.md",
                     "docs/a.md", "some/repo/.claude/x.md"):
            with self.subTest(path=path):
                self.assertTrue(pretool_gate.is_bookkeeping(path))

    def test_application_source_is_not_bookkeeping(self):
        for path in ("src/app/main.py", "app.py", str(state.ROOT / "app.py")):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_bookkeeping(path))


class FrozenWindowTest(unittest.TestCase):
    """The settings.json hook also fires inside subagents, as handle_edit(orch=False).
    During a review (or handoff debt) only bookkeeping may pass; it must include
    the files the docs profile owns, and nothing that is code."""

    def in_review(self):
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch.object(state, "connect"))
        stack.enter_context(unittest.mock.patch.object(
            pretool_gate, "active_review_run", return_value=REVIEW_RUN))
        return stack

    def under_handoff_debt(self):
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch.object(state, "connect"))
        stack.enter_context(unittest.mock.patch.object(
            pretool_gate, "active_review_run", return_value=None))
        stack.enter_context(unittest.mock.patch.object(
            pretool_gate, "handoff_freeze_task", return_value="task-0899"))
        return stack

    def test_a_docs_write_to_root_claude_md_passes_the_review_freeze(self):
        with self.in_review():
            code, err = quiet(pretool_gate.handle_edit, ROOT_CLAUDE_MD)
        self.assertEqual(0, code, err)

    def test_a_docs_write_to_a_readme_passes_the_review_freeze(self):
        for path in ("README.md", "pkg/README.rst", "README"):
            with self.subTest(path=path), self.in_review():
                code, err = quiet(pretool_gate.handle_edit, path)
                self.assertEqual(0, code, err)

    def test_root_claude_md_and_readme_pass_handoff_debt(self):
        for path in (ROOT_CLAUDE_MD, "README.md"):
            with self.subTest(path=path), self.under_handoff_debt():
                code, err = quiet(pretool_gate.handle_edit, path)
                self.assertEqual(0, code, err)

    def test_application_source_is_still_frozen_in_review(self):
        for path in ("src/app/main.py", TRAVERSAL, "readme_gen.py",
                     str(state.ROOT / "src" / "app" / "CLAUDE.md")):
            with self.subTest(path=path), self.in_review():
                code, err = quiet(pretool_gate.handle_edit, path)
                self.assertEqual(2, code)
                self.assertIn("read-only 'review' stage", err)

    def test_application_source_is_still_frozen_under_handoff_debt(self):
        for path in ("src/app/main.py", TRAVERSAL):
            with self.subTest(path=path), self.under_handoff_debt():
                code, err = quiet(pretool_gate.handle_edit, path)
                self.assertEqual(2, code)
                self.assertIn("Handoff debt", err)


class EditToolTargetTest(unittest.TestCase):
    def test_the_edit_tool_list_names_all_four(self):
        self.assertEqual(set(EDIT_TOOL_NAMES), set(pretool_gate.EDIT_TOOLS))

    def test_edit_and_write_targets(self):
        self.assertEqual(("a.md", "new"), pretool_gate.edit_target(
            "Edit", {"file_path": "a.md", "new_string": "new"}))
        self.assertEqual(("a.md", "body"), pretool_gate.edit_target(
            "Write", {"file_path": "a.md", "content": "body"}))

    def test_multiedit_target_joins_every_edit(self):
        path, content = pretool_gate.edit_target("MultiEdit", {
            "file_path": "a.md",
            "edits": [{"old_string": "x", "new_string": "one"},
                      {"old_string": "y", "new_string": "two"}]})
        self.assertEqual("a.md", path)
        self.assertIn("one", content)
        self.assertIn("two", content)

    def test_multiedit_with_a_malformed_edit_list_does_not_raise(self):
        for edits in (None, "x", [None, 3], {"new_string": "x"}):
            with self.subTest(edits=edits):
                path, _ = pretool_gate.edit_target(
                    "MultiEdit", {"file_path": "a.md", "edits": edits})
                self.assertEqual("a.md", path)

    def test_notebook_edit_target_reads_notebook_path_and_new_source(self):
        self.assertEqual(("n.ipynb", "print(1)"), pretool_gate.edit_target(
            "NotebookEdit", {"notebook_path": "n.ipynb", "new_source": "print(1)"}))


class ProtectedStateAcrossEditToolsTest(unittest.TestCase):
    """The .agentry/state/ and pipeline.json protection must hold for every tool
    that writes a file, not only the two the matcher used to name."""

    PROTECTED = (".agentry/state/approvals", "docs/../.agentry/state/mode",
                 ".agentry/pipeline.json", "src/.agentry/pipeline.json")

    @staticmethod
    def payload(tool: str, path: str) -> dict:
        if tool == "NotebookEdit":
            return {"notebook_path": path, "new_source": "x"}
        if tool == "MultiEdit":
            return {"file_path": path, "edits": [{"old_string": "a", "new_string": "b"}]}
        return {"file_path": path, "new_string": "b", "content": "b"}

    def test_every_profile_denies_every_write_tool_on_protected_state(self):
        for handler in (agent_gate.handle_dev, agent_gate.handle_docs,
                        agent_gate.handle_readonly):
            for tool in EDIT_TOOL_NAMES:
                for path in self.PROTECTED:
                    with self.subTest(handler=handler.__name__, tool=tool, path=path):
                        code, _ = quiet(handler, tool, self.payload(tool, path), cwd=".")
                        self.assertEqual(2, code)

    def test_docs_multiedit_to_agentry_state_is_denied_with_the_state_reason(self):
        code, err = quiet(agent_gate.handle_docs, "MultiEdit",
                          self.payload("MultiEdit", ".agentry/state/approvals"), cwd=".")
        self.assertEqual(2, code)
        self.assertIn("is under .agentry/state/", err)

    def test_the_real_hook_denies_docs_multiedit_and_notebookedit_on_state(self):
        target = str(state.ROOT / ".agentry" / "state" / "mode")
        for tool in ("MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                body = {"tool_name": tool, "cwd": str(state.ROOT),
                        "tool_input": self.payload(tool, target)}
                proc = subprocess.run(
                    [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), "--profile", "docs"],
                    input=json.dumps(body).encode("utf-8"), capture_output=True, timeout=30)
                self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_docs_multiedit_and_notebookedit_keep_the_docs_scope(self):
        for tool in ("MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                ok, err = quiet(agent_gate.handle_docs, tool,
                                self.payload(tool, "docs/technical/a.md"), cwd=".")
                self.assertEqual(0, ok, err)
                bad, _ = quiet(agent_gate.handle_docs, tool,
                               self.payload(tool, "src/app/main.py"), cwd=".")
                self.assertEqual(2, bad)

    def test_readonly_is_denied_every_write_tool(self):
        for tool in EDIT_TOOL_NAMES:
            with self.subTest(tool=tool):
                code, _ = quiet(agent_gate.handle_readonly, tool,
                                self.payload(tool, "docs/a.md"), cwd=".")
                self.assertEqual(2, code)

    def test_the_dash_rule_reaches_multiedit_and_notebookedit(self):
        multi = {"file_path": "docs/a.md",
                 "edits": [{"old_string": "a", "new_string": "x " + EM_DASH + " y"}]}
        note = {"notebook_path": "n.ipynb", "new_source": "x " + EM_DASH + " y"}
        for tool, ti in (("MultiEdit", multi), ("NotebookEdit", note)):
            with self.subTest(tool=tool):
                path, content = pretool_gate.edit_target(tool, ti)
                code, err = quiet(pretool_gate.handle_edit, path, content)
                self.assertEqual(2, code)
                self.assertIn("dash", err)

    def test_the_main_thread_hook_handles_multiedit(self):
        body = {"tool_name": "MultiEdit", "cwd": str(state.ROOT),
                "tool_input": {"file_path": "docs/a.md",
                               "edits": [{"old_string": "a", "new_string": "x" + EM_DASH}]}}
        proc = subprocess.run([sys.executable, str(PIPELINE_DIR / "pretool_gate.py")],
                              input=json.dumps(body).encode("utf-8"),
                              capture_output=True, timeout=30)
        self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))


class HookMatchersCoverEveryWriteToolTest(unittest.TestCase):
    """A hook that never fires is no gate. Every matcher wired to a gate script must
    fullmatch all four file-writing tools (claude.exe 2.1.295 lists them as
    Write, Edit, MultiEdit, NotebookEdit)."""

    def test_the_agent_gate_matcher_covers_the_write_tools(self):
        # The per-agent `hooks:` blocks are gone (task-0038): agent_gate is registered once,
        # plugin-wide, and a matcher that misses a write tool leaves every agent unchecked.
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        groups = [g for g in hooks["PreToolUse"]
                  if any("agent_gate.py" in h.get("command", "") for h in g["hooks"])]
        self.assertEqual(1, len(groups))
        # WebSearch and WebFetch: the readonly and docs profiles audit-log them and the dev
        # profile refuses them; the per-agent blocks of the four web agents carried them.
        for tool in ("Bash", *EDIT_TOOL_NAMES, "WebSearch", "WebFetch"):
            with self.subTest(tool=tool):
                self.assertRegex(tool, f"^(?:{groups[0]['matcher']})$")

    def test_the_pretool_gate_matcher_covers_the_write_tools(self):
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        groups = [g for g in hooks["PreToolUse"]
                  if any("pretool_gate.py" in h.get("command", "") for h in g["hooks"])]
        self.assertEqual(1, len(groups))
        for tool in ("Bash", *EDIT_TOOL_NAMES):
            with self.subTest(tool=tool):
                self.assertRegex(tool, f"^(?:{groups[0]['matcher']})$")


if __name__ == "__main__":
    unittest.main()

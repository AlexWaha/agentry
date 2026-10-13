"""task-0134 item 2: a subagent writes only inside its own task worktree.

The dispatch prompt of an implement, test or docs agent opens with `Task worktree: task-NNNN`
alone on a line. agent_gate reads it from the agent's OWN transcript (the way `Swarm zone:` is
read) and, while worktrees are in play, refuses a write outside that task's tree. Everything
runs against real git worktrees (test_worktree_core.WorkspaceCase) and real transcript files;
state.ROOT, the run store and pipeline.json are the sandbox's.
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

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import gate
import pretool_gate
import tmproot
import worktree
import zones
from test_worktree_core import OTHER, TASK, WorkspaceCase

SESSION = "932b93d4-360c-4529-87ee-75d3a6d89557"
AGENT_A = "a3d9c94df3116b37f"
AGENT_B = "b44e1c0a99d2f7e10"
AGENT_C = "c55f2d1baae3a8f21"
NONE = "no-identity"


class ConfinementCase(WorkspaceCase):
    """TASK and OTHER each live in their own tree; AGENT_A is dispatched for TASK, AGENT_B for
    OTHER, AGENT_C names no tree."""

    def setUp(self):
        super().setUp()
        self.transcripts = self.root / "claude-projects"
        self.make_tree(TASK)
        self.make_tree(OTHER)
        self.tree = self.root / "src-wt" / TASK
        self.other_tree = self.root / "src-wt" / OTHER
        self.cwd = str(self.root)
        (self.root / "tmp").mkdir(exist_ok=True)
        self.agent(AGENT_A, f"Task worktree: {TASK}\nImplement the thing.")
        self.agent(AGENT_B, f"Task worktree: {OTHER}\nImplement the other thing.")
        self.agent(AGENT_C, "Implement the thing, no worktree named.")

    # --- fixtures ---

    @property
    def parent_transcript(self) -> Path:
        return self.transcripts / "proj" / f"{SESSION}.jsonl"

    def agent(self, agent_id: str, prompt: str) -> None:
        """The agent's own transcript: line 1 is the dispatch prompt."""
        sub = self.parent_transcript.parent / SESSION / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        line = {"isSidechain": True, "type": "user", "message": {"role": "user", "content": prompt}}
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    def payload(self, agent_id: str = AGENT_A, **over) -> dict:
        data = {"session_id": SESSION, "transcript_path": str(self.parent_transcript),
                "cwd": self.cwd, "agent_id": agent_id, "agent_type": "senior-backend-dev"}
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    # --- calls ---

    def call(self, handler, tool, ti, agent_id=AGENT_A, cwd=None, payload="default"):
        payload = self.payload(agent_id) if payload == "default" else payload
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = handler(tool, ti, cwd=cwd if cwd is not None else self.cwd, payload=payload)
        return code, err.getvalue()

    def write(self, path, tool="Write", handler=None, **kw):
        field = "content" if tool == "Write" else "new_string"
        return self.call(handler or agent_gate.handle_dev, tool, {"file_path": str(path), field: "x"}, **kw)

    def bash(self, command, **kw):
        return self.call(agent_gate.handle_dev, "Bash", {"command": command}, **kw)

    def denial(self, tool, ti, agent_id=AGENT_A, cwd=None) -> str:
        """agent_gate.worktree_denial alone: '' when it allows, else its text."""
        return agent_gate.worktree_denial("Dev agent", tool, ti, cwd or self.cwd, self.payload(agent_id))

    def assertAllowed(self, result):
        self.assertEqual(0, result[0], result[1])

    def assertDenied(self, result, *needles):
        code, err = result
        self.assertEqual(2, code, err)
        for needle in needles:
            self.assertIn(needle, err)


class NamedCasesTest(ConfinementCase):

    def test_write_inside_own_worktree_allowed(self):
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.write(self.tree / "README.md", tool))
        self.assertAllowed(self.write(self.tree / "deep" / "er" / "new.py"))
        self.assertAllowed(self.bash(f'echo hi > "{self.tree.as_posix()}/out.txt"'))
        self.assertAllowed(self.bash(f'cd "{self.tree.as_posix()}" && touch new.txt'))

    def test_write_into_other_task_worktree_denied(self):
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assertDenied(self.write(self.other_tree / "README.md", tool),
                                  f"worktree of {OTHER}", f"({TASK})")
        self.assertDenied(self.bash(f'echo hi > "{self.other_tree.as_posix()}/out.txt"'), OTHER)
        multi = {"file_path": str(self.other_tree / "a.py"), "edits": [{"new_string": "x"}]}
        self.assertDenied(self.call(agent_gate.handle_dev, "MultiEdit", multi), OTHER)
        notebook = {"notebook_path": str(self.other_tree / "n.ipynb"), "new_source": "x"}
        self.assertDenied(self.call(agent_gate.handle_dev, "NotebookEdit", notebook), OTHER)

    def test_write_without_task_worktree_line_denied_when_enabled(self):
        self.assertDenied(self.write(self.tree / "README.md", agent_id=AGENT_C),
                          "names no task worktree", "Task worktree: task-NNNN")
        self.assertDenied(self.bash(f'echo hi > "{self.tree.as_posix()}/out.txt"', agent_id=AGENT_C),
                          "names no task worktree")

    def test_confinement_inactive_when_worktrees_disabled(self):
        self.config("off")
        for rel in ("src-wt/task-0130", "src-wt/task-0131"):
            self.sql("UPDATE runs SET worktree = '' WHERE worktree = ?", rel)
        self.assertFalse(pretool_gate.worktrees_active())
        self.assertAllowed(self.write(self.other_tree / "README.md"))
        self.assertAllowed(self.write(self.tree / "README.md", agent_id=AGENT_C))
        self.assertEqual("", self.denial("Write", {"file_path": str(self.other_tree / "README.md")}))
        self.assertAllowed(self.bash(f'echo hi > "{self.other_tree.as_posix()}/out.txt"'))


class WhereTheLineAppliesTest(ConfinementCase):

    def test_a_recorded_tree_keeps_the_confinement_when_the_flag_is_turned_off(self):
        self.config("off")
        self.assertDenied(self.write(self.other_tree / "README.md"), OTHER)

    def test_a_finished_run_leaves_no_confinement(self):
        self.config("off")
        self.sql("UPDATE runs SET stage = 'done'")
        self.assertFalse(pretool_gate.worktrees_active())
        self.assertAllowed(self.write(self.other_tree / "README.md"))

    def test_a_read_is_never_judged(self):
        for command in (f'cat "{self.other_tree.as_posix()}/README.md"', "ls", "git status"):
            with self.subTest(command=command):
                self.assertEqual("", self.denial("Bash", {"command": command}, AGENT_C))
        self.assertEqual("", self.denial("Read", {"file_path": str(self.other_tree / "README.md")}, AGENT_C))

    def test_the_workspace_scratch_and_bookkeeping_are_outside_every_tree(self):
        self.assertAllowed(self.write(self.root / "tmp" / "log.txt"))
        self.assertAllowed(self.write(self.root / "tmp" / "log.txt", agent_id=AGENT_C))
        self.assertAllowed(self.bash("echo hi > tmp/out.log"))

    def test_the_trunk_checkout_is_outside_the_tree_of_an_identified_agent(self):
        reason = self.denial("Write", {"file_path": str(self.src / "README.md")})
        self.assertIn("outside your task worktree", reason)
        self.assertIn(TASK, reason)
        self.assertIn("outside your task worktree", self.denial(
            "Bash", {"command": f'echo hi > "{self.src.as_posix()}/x.txt"'}))

    def test_an_agent_without_a_line_may_still_write_outside_every_tree(self):
        self.assertEqual("", self.denial("Write", {"file_path": str(self.src / "README.md")}, AGENT_C))
        self.assertEqual("", self.denial("Write", {"file_path": str(self.root / "notes.txt")}, AGENT_C))

    def test_a_missing_payload_is_not_checked(self):
        self.assertEqual("", agent_gate.worktree_denial(
            "Dev agent", "Write", {"file_path": str(self.other_tree / "x")}, self.cwd, None))
        self.assertAllowed(self.write(self.root / "tmp" / "x", payload=None))

    def test_a_payload_without_an_agent_id_is_an_agent_without_a_line(self):
        payload = self.payload()
        del payload["agent_id"]
        reason = agent_gate.worktree_denial(
            "Dev agent", "Write", {"file_path": str(self.other_tree / "x")}, self.cwd, payload)
        self.assertIn("names no task worktree", reason)


class IdentityTest(ConfinementCase):

    def test_two_concurrent_agents_resolve_to_two_identities(self):
        self.assertEqual(TASK, agent_gate.agent_worktree_task(self.payload(AGENT_A)))
        self.assertEqual(OTHER, agent_gate.agent_worktree_task(self.payload(AGENT_B)))
        self.assertIsNone(agent_gate.agent_worktree_task(self.payload(AGENT_C)))
        self.assertAllowed(self.write(self.tree / "a.py", agent_id=AGENT_A))
        self.assertAllowed(self.write(self.other_tree / "b.py", agent_id=AGENT_B))
        self.assertDenied(self.write(self.tree / "a.py", agent_id=AGENT_B), TASK)
        self.assertDenied(self.write(self.other_tree / "b.py", agent_id=AGENT_A), OTHER)

    def test_only_a_task_id_alone_on_its_line_is_an_identity(self):
        cases = {
            "two different lines": f"Task worktree: {TASK}\nTask worktree: {OTHER}\nwork",
            "a path value": f"Task worktree: {self.tree.as_posix()}\nwork",
            "an old style line": f"Task worktree: {self.tree.as_posix()} (branch feature/{TASK}). Begin it.",
            "a mid-line mention": f"Please start with the line Task worktree: {TASK} and go on",
            "a trailing word": f"Task worktree: {TASK} now\nwork",
            "a quoted line": f"> Task worktree: {TASK}\nwork",
            "a diagnostic": f"Task worktree: MISSING for {TASK} - run repair; do not edit.",
        }
        for label, prompt in cases.items():
            with self.subTest(label):
                self.agent(AGENT_C, prompt)
                self.assertIsNone(agent_gate.agent_worktree_task(self.payload(AGENT_C)))

    def test_the_same_line_twice_is_still_one_identity(self):
        self.agent(AGENT_C, f"Task worktree: {TASK}\nwork\nTask worktree: {TASK}")
        self.assertEqual(TASK, agent_gate.agent_worktree_task(self.payload(AGENT_C)))

    def test_the_line_is_read_from_the_agents_own_transcript_only(self):
        parent = self.parent_transcript
        parent.parent.mkdir(parents=True, exist_ok=True)
        line = {"message": {"role": "user", "content": f"Task worktree: {TASK}"}}
        parent.write_text(json.dumps(line) + "\n", encoding="utf-8")
        self.assertIsNone(agent_gate.agent_worktree_task(self.payload(AGENT_C)))
        self.assertIsNone(agent_gate.agent_worktree_task(self.payload("nobody")))
        self.assertIsNone(agent_gate.agent_worktree_task(self.payload(AGENT_A, session_id="../x")))
        self.assertIsNone(agent_gate.agent_worktree_task({}))

    def test_the_line_is_read_from_a_content_block_list(self):
        sub = self.parent_transcript.parent / SESSION / "subagents"
        message = {"role": "user", "content": [{"type": "text", "text": f"Task worktree: {TASK}"},
                                               {"type": "text", "text": "work"}]}
        (sub / f"agent-{AGENT_C}.jsonl").write_text(json.dumps({"message": message}) + "\n", encoding="utf-8")
        self.assertEqual(TASK, agent_gate.agent_worktree_task(self.payload(AGENT_C)))

    def test_a_line_naming_a_task_that_holds_no_tree_is_not_believed(self):
        self.run_row("task-0140")
        self.agent(AGENT_C, "Task worktree: task-0140\nwork")
        target = {"file_path": str(self.tree / "x")}
        self.assertIn("names no task worktree", self.denial("Write", target, AGENT_C))
        self.agent(AGENT_C, "Task worktree: task-0999\nwork")
        self.assertIn("names no task worktree", self.denial("Write", target, AGENT_C))

    def test_a_line_naming_a_task_whose_run_left_the_editing_stages_is_not_believed(self):
        self.sql("UPDATE runs SET stage = 'ready' WHERE task = ?", TASK)
        self.assertFalse(worktree.holds_tree(TASK))
        self.assertIn("names no task worktree", self.denial("Write", {"file_path": str(self.tree / "x")}))
        self.sql("UPDATE runs SET stage = 'review' WHERE task = ?", TASK)
        self.assertTrue(worktree.holds_tree(TASK))
        self.assertEqual("", self.denial("Write", {"file_path": str(self.tree / "x")}))

    def test_a_line_naming_a_task_whose_tree_is_gone_is_not_believed(self):
        tmproot.rmtree(self.tree)
        self.assertFalse(worktree.holds_tree(TASK))

    def test_zone_reading_is_unchanged(self):
        sub = self.parent_transcript.parent / SESSION / "subagents"
        self.agent(AGENT_C, f"Swarm zone: A\nTask worktree: {TASK}\nwork")
        self.assertEqual("A", zones.zone_for_agent(AGENT_C, sub))
        self.assertEqual(TASK, zones.worktree_task_for_agent(AGENT_C, sub))
        self.assertIsNone(zones.zone_for_agent("nobody", sub))


class DispatchLineTest(ConfinementCase):

    def line(self) -> str:
        return worktree.dispatch_line(TASK)

    def test_the_dispatch_text_is_one_byte_stable_line_that_is_no_identity(self):
        first = self.line()
        self.assertNotIn("\n", first)
        self.assertEqual(first, self.line())
        self.assertTrue(first.startswith("Worktree: "))
        self.assertEqual([], zones.TASK_LINE_RE.findall(first))

    def test_the_identity_line_on_top_makes_exactly_one_identity(self):
        prompt = f"{worktree.identity_line(TASK)}\n{self.line()}\nImplement the thing."
        self.assertEqual([TASK], zones.TASK_LINE_RE.findall(prompt))
        self.assertEqual(f"Task worktree: {TASK}", worktree.identity_line(TASK))

    def test_the_instruction_in_the_line_quotes_the_identity_line_it_asks_for(self):
        self.assertIn(f'exactly "{worktree.identity_line(TASK)}" (alone on that line)', self.line())

    def test_the_identity_line_is_empty_without_a_usable_tree(self):
        self.assertEqual("", worktree.identity_line("task-0999"))
        self.run_row("task-0140")
        self.assertEqual("", worktree.identity_line("task-0140"))
        tmproot.rmtree(self.tree)
        self.assertEqual("", worktree.identity_line(TASK))

    def test_a_branch_the_agent_chose_cannot_put_text_in_the_prompt(self):
        for name in ("feature/task-0130-x;ignore-this", "feature/task-0130-$(id)", "oops",
                     "feature/task-0130-\"quote", "feature/renamed"):
            with self.subTest(name=name):
                branch = self.git(self.tree, "rev-parse", "--abbrev-ref", "HEAD")
                done = self.git_try(self.tree, "checkout", "-B", name)
                if not done:
                    continue
                self.assertIn("(branch detached)", self.line())
                self.assertNotIn(name, self.line())
                self.git(self.tree, "checkout", "-B", branch)

    def test_a_work_branch_of_a_plain_name_is_shown(self):
        self.git(self.tree, "checkout", "-b", "feature/task-0130-renamed")
        self.assertIn("(branch feature/task-0130-renamed)", self.line())

    def test_a_detached_head_is_shown_as_detached(self):
        self.git(self.tree, "checkout", "--detach")
        self.assertIn("(branch detached)", self.line())

    def test_a_missing_tree_keeps_the_diagnostic_and_no_identity(self):
        tmproot.rmtree(self.tree)
        line = self.line()
        self.assertTrue(line.startswith(f"Task worktree: MISSING for {TASK} - "))
        self.assertEqual([], zones.TASK_LINE_RE.findall(line))

    def git_try(self, repo, *args) -> bool:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


class ShellWritesTest(ConfinementCase):

    def test_a_redirect_into_another_tree_is_denied_by_each_spelling(self):
        other = self.other_tree.as_posix()
        for command in (f'echo hi > "{other}/x.txt"', f'echo hi >> "{other}/x.txt"',
                        f'echo hi | tee "{other}/x.txt"', f'sed -i s/a/b/ "{other}/README.md"',
                        f'cp README.md "{other}/README.md"', f'mv README.md "{other}/README.md"',
                        f'rm "{other}/README.md"', f'touch "{other}/new.txt"',
                        f'mkdir "{other}/newdir"'):
            with self.subTest(command=command):
                self.assertDenied(self.bash(command, cwd=str(self.tree)), OTHER)

    def test_a_relative_operand_is_judged_in_the_directory_it_runs_in(self):
        self.assertAllowed(self.bash("touch new.txt", cwd=str(self.tree)))
        self.assertDenied(self.bash("touch new.txt", cwd=str(self.other_tree)), OTHER)
        self.assertDenied(self.bash(f'cd "{self.other_tree.as_posix()}" && echo hi | tee x.txt'), OTHER)

    def test_dotdot_and_case_spellings_are_judged_where_they_land(self):
        sneaky = f"{self.tree.as_posix()}/../{OTHER}/x.txt"
        self.assertDenied(self.write(sneaky), OTHER)
        self.assertDenied(self.write(str(self.other_tree / "x.txt").upper()), OTHER)

    def test_extended_length_and_msys_spellings_are_judged_where_they_land(self):
        extended = "\\\\?\\" + str(self.other_tree / "x.txt")
        self.assertIn(f"worktree of {OTHER}", self.denial("Write", {"file_path": extended}))
        drive, rest = str(self.other_tree / "x.txt").replace("\\", "/").split(":", 1)
        msys = f"/{drive.lower()}{rest}"
        self.assertIn(f"worktree of {OTHER}", self.denial("Write", {"file_path": msys}))
        self.assertEqual("", self.denial("Write", {"file_path": "\\\\?\\" + str(self.tree / "x.txt")}))

    def test_a_network_path_cannot_be_placed_and_is_denied(self):
        reason = self.denial("Write", {"file_path": "//server/share/x.txt"})
        self.assertIn("cannot place", reason)

    def test_a_glob_that_reaches_another_tree_is_denied(self):
        reason = self.denial("Bash", {"command": f'rm {self.root.as_posix()}/src-wt/task-0131/READ*'})
        self.assertIn(OTHER, reason)

    def test_an_operand_the_shell_expands_cannot_be_placed_and_is_denied(self):
        for command in ('echo hi > "$OUT"', "echo hi > `pwd`/x", "touch ~/x"):
            with self.subTest(command=command):
                self.assertIn("cannot place", self.denial("Bash", {"command": command}))

    def test_git_that_changes_another_tree_is_denied_by_its_placement(self):
        other = self.other_tree.as_posix()
        for command in (f'git -C "{other}" add .', f'git -C "{other}" commit -m x',
                        f'cd "{other}" && git add .', f'cd "{other}" && git checkout -- README.md',
                        f'git -C "{other}" reset --hard', f'git -C "{self.src.as_posix()}" merge x'):
            with self.subTest(command=command):
                self.assertNotEqual("", self.denial("Bash", {"command": command}), command)

    def test_git_in_its_own_tree_and_read_only_git_elsewhere_are_not_judged(self):
        own = self.tree.as_posix()
        for command in (f'git -C "{own}" add .', f'cd "{own}" && git commit -m x',
                        f'git -C "{self.other_tree.as_posix()}" status',
                        f'git -C "{self.other_tree.as_posix()}" diff HEAD', "git log --oneline"):
            with self.subTest(command=command):
                self.assertEqual("", self.denial("Bash", {"command": command}), command)

    def test_git_that_moves_the_repository_or_its_place_is_refused(self):
        own = self.tree.as_posix()
        for command in (f'git --git-dir "{self.other_tree.as_posix()}/.git" add .',
                        f'GIT_DIR={self.other_tree.as_posix()}/.git git add .',
                        'cd "$X" && git add .'):
            with self.subTest(command=command):
                self.assertIn("cannot place", self.denial("Bash", {"command": command}, cwd=own))

    def test_a_nested_shell_body_is_judged_too(self):
        other = self.other_tree.as_posix()
        self.assertIn(OTHER, self.denial("Bash", {"command": f'bash -c \'echo hi > "{other}/x.txt"\''}))


class DocsProfileTest(ConfinementCase):

    def docs(self, path, agent_id=AGENT_A, **kw):
        return self.write(path, handler=agent_gate.handle_docs, agent_id=agent_id, **kw)

    def test_a_docs_agent_writes_docs_in_its_own_tree(self):
        self.assertAllowed(self.docs(self.tree / "docs" / "guide.md"))

    def test_a_docs_agent_cannot_write_docs_into_another_tree(self):
        self.assertDenied(self.docs(self.other_tree / "docs" / "guide.md"), OTHER)

    def test_a_docs_agent_cannot_write_docs_into_the_trunk_checkout(self):
        self.assertDenied(self.docs(self.src / "docs" / "guide.md"), "outside your task worktree")

    def test_a_docs_agent_without_a_line_is_kept_out_of_every_tree(self):
        self.assertDenied(self.docs(self.tree / "docs" / "guide.md", agent_id=AGENT_C),
                          "names no task worktree")

    def test_a_docs_agent_keeps_the_workspace_docs_folders(self):
        plans = self.root / ".agentry" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        self.assertAllowed(self.docs(plans / "2026-10-13-x-task-0130.md"))
        self.assertAllowed(self.docs(plans / "2026-10-13-x-task-0130.md", agent_id=AGENT_C))

    def test_the_docs_scope_still_applies_first(self):
        self.assertDenied(self.docs(self.tree / "app" / "code.py"), "writes allowed only under")

    def test_the_docs_profile_is_wired_to_pass_the_payload(self):
        payload = self.payload(AGENT_A)
        target = str(self.other_tree / "docs" / "g.md")
        payload.update({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}})
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "read_payload", return_value=payload), \
                unittest.mock.patch.object(sys, "argv", ["agent_gate", "--profile", "docs"]), \
                contextlib.redirect_stderr(err):
            code = agent_gate.evaluate()
        self.assertEqual(2, code, err.getvalue())
        self.assertIn(OTHER, err.getvalue())


class FailClosedTest(ConfinementCase):

    def test_an_unreadable_run_store_denies_every_write(self):
        with unittest.mock.patch.object(worktree, "_rows", return_value=None), \
                unittest.mock.patch.object(worktree, "READ_RETRY_PAUSE", 0):
            self.assertDenied(self.write(self.tree / "README.md"), "refuses rather than guess")
            self.assertDenied(self.bash("touch tmp/x"), "refuses rather than guess")

    def test_a_doubt_from_the_owner_lookup_denies(self):
        with unittest.mock.patch.object(worktree, "owner_of", side_effect=worktree.Doubt("x")):
            self.assertDenied(self.write(self.tree / "README.md"), "refuses rather than guess")

    def test_a_worktree_module_that_cannot_be_imported_denies_a_subagent_write(self):
        with unittest.mock.patch.dict(sys.modules, {"worktree": None}):
            self.assertDenied(self.write(self.tree / "README.md"), "refuses rather than guess")
            self.assertDenied(self.write(self.tree / "README.md", handler=agent_gate.handle_docs),
                              "refuses rather than guess")

    def test_a_check_that_raises_denies_instead_of_falling_open(self):
        with unittest.mock.patch.object(agent_gate, "worktree_targets", side_effect=RuntimeError("boom")):
            self.assertDenied(self.write(self.tree / "README.md"), "the check itself failed")

    def test_a_pretool_gate_without_the_flag_functions_reads_as_in_play(self):
        self.config("off")
        self.sql("UPDATE runs SET stage = 'done'")
        self.assertAllowed(self.bash('echo hi > "$OUT"'))
        with unittest.mock.patch.object(pretool_gate, "worktrees_active", side_effect=AttributeError("gone")):
            self.assertTrue(agent_gate.worktree_flag("worktrees_active"))
            self.assertDenied(self.bash('echo hi > "$OUT"'), "refuses rather than guess")
        with unittest.mock.patch.object(pretool_gate, "worktrees_active", new=None):
            self.assertTrue(agent_gate.worktree_flag("worktrees_active"))
        self.assertTrue(agent_gate.worktree_flag("no_such_function"))

    def test_the_prelude_checks_do_not_raise_when_the_flag_functions_fail(self):
        for name in ("worktrees_active", "worktrees_on"):
            with self.subTest(name=name), unittest.mock.patch.object(
                    pretool_gate, name, side_effect=AttributeError("gone")):
                with contextlib.redirect_stderr(io.StringIO()):
                    layout = agent_gate.bash_prelude("Dev agent", "git worktree remove x", self.cwd)
                    branch_cmd = f'git -C "{self.src.as_posix()}" checkout -b feature/task-0999'
                    moved = agent_gate.bash_prelude("Dev agent", branch_cmd, self.cwd)
                self.assertEqual((2, 2), (layout, moved))
                self.assertEqual(0, agent_gate.bash_prelude("Dev agent", "ls", self.cwd))

    def test_the_glob_deadline_still_reaches_its_own_message(self):
        with unittest.mock.patch.object(agent_gate, "glob_hits", side_effect=agent_gate.GlobDeadline("p")):
            with self.assertRaises(agent_gate.GlobDeadline):
                self.denial("Bash", {"command": f"rm {self.tree.as_posix()}/R*"})


PY = f'"{sys.executable}"'
HARNESS_FILES = ("artifact_gate.py", "ui_evidence.py", "state.py")
PLAN_TEXT = "A plan that says what changes and why, long enough to be a real document. " * 8


class RootSwitchTest(WorkspaceCase):
    """The two gates that read workspace state (the plan document, the UI evidence) run from
    `{root}`: inside a tree their own copy would resolve state.ROOT to the tree."""

    def copy_harness(self, where: Path) -> None:
        target = where / ".claude" / "tools" / "pipeline"
        target.mkdir(parents=True, exist_ok=True)
        for name in HARNESS_FILES:
            text = (PIPELINE_DIR / name).read_text(encoding="utf-8")
            (target / name).write_text(text, encoding="utf-8")

    def setUp(self):
        super().setUp()
        self.copy_harness(self.root)  # the live harness: ROOT is the workspace
        self.copy_harness(self.src)  # tracked in the repo, so the tree holds a copy of its own
        self.git(self.src, "add", ".")
        self.git(self.src, "commit", "-m", "harness copy")
        self.make_tree(TASK, "test")
        plans = self.root / ".agentry" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / f"2026-10-13-x-{TASK}.md").write_text(PLAN_TEXT, encoding="utf-8")
        evidence = self.root / ".agentry" / "state" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / f"{TASK}.json").write_text(
            json.dumps({"task": TASK, "not_applicable": "harness only"}), encoding="utf-8")

    def run_gate(self, script: str, args: str, rooted: bool) -> dict:
        where = "{root}/.claude/tools/pipeline/" if rooted else ".claude/tools/pipeline/"
        cmd = f"{PY} {where}{script} --task {{task}} {args}".strip()
        pipeline = {"cwd": "src", "pipelines": {"build": {"stages": [
            {"name": "test", "owner": "qa-engineer", "exit_gate": {"cmd": cmd, "expect_exit": 0}}]}}}
        return gate.run_gate(pipeline, "test", TASK)

    def test_the_plan_gate_reads_the_workspace_plans_from_inside_a_tree(self):
        self.assertTrue(self.run_gate("artifact_gate.py", "--kind plan", rooted=True)["passed"])

    def test_the_plan_gate_without_the_switch_reads_the_tree_and_fails(self):
        result = self.run_gate("artifact_gate.py", "--kind plan", rooted=False)
        self.assertFalse(result["passed"], result)

    def test_the_ui_evidence_gate_reads_the_workspace_evidence_from_inside_a_tree(self):
        self.assertTrue(self.run_gate("ui_evidence.py", "", rooted=True)["passed"])

    def test_the_ui_evidence_gate_without_the_switch_reads_the_tree_and_fails(self):
        result = self.run_gate("ui_evidence.py", "", rooted=False)
        self.assertFalse(result["passed"], result)

    def test_the_shipped_config_runs_exactly_those_two_gates_from_the_root(self):
        shipped = PIPELINE_DIR.parents[2] / ".agentry" / "pipeline.json"
        config = json.loads(shipped.read_text(encoding="utf-8"))
        stages = {s["name"]: s for s in config["pipelines"]["build"]["stages"]}
        plan = stages["plan"]["exit_gate"]["cmd"]
        test = stages["test"]["exit_gate"]["cmd"]
        self.assertIn("python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan", plan)
        self.assertIn("python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}", test)
        for name, stage in stages.items():
            cmd = stage.get("exit_gate", {}).get("cmd", "")
            for script in ("artifact_gate.py", "ui_evidence.py"):
                if script in cmd:
                    self.assertIn(f"{{root}}/.claude/tools/pipeline/{script}", cmd, name)


class NoDashTest(unittest.TestCase):

    def test_the_new_text_has_no_long_dash(self):
        dashes = {chr(0x2014), chr(0x2013)}
        for name in ("agent_gate.py", "worktree.py", "zones.py"):
            text = (PIPELINE_DIR / name).read_text(encoding="utf-8")
            self.assertFalse(dashes & set(text), name)
        self.assertFalse(dashes & set(Path(__file__).read_text(encoding="utf-8")))

    def test_the_identity_grammar_tolerates_blanks_around_the_line(self):
        self.assertEqual(["task-0134"], zones.TASK_LINE_RE.findall("x\n  Task worktree:task-0134  \ny"))


if __name__ == "__main__":
    unittest.main()

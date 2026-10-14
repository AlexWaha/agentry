"""task-0060 S3: the swarm zone check in agent_gate (dev profile).

While a frozen zone map is in force a dev agent writes only inside the zone its own
dispatch prompt names, or under tmp/. The zone is read from the agent's OWN transcript
(`<transcript dir>/<session id>/subagents/agent-<id>.jsonl`), never from the parent
transcript, which names every agent's zone. With no map, or a map whose task is outside
implement/test, dev behaves exactly as before. A map that exists but cannot be used, an
agent with no readable zone line, and the git commands that rewrite the tree all deny.

Every fixture is a sandbox: `state.ROOT`, `STATE_DIR`, `DB_PATH` and `PIPELINE_PATH` are
patched onto a throwaway project, so nothing here touches the real tree or run.db.
"""

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

import agent_gate
import pretool_gate
import state
import tmproot
import zones

TASK = "task-0120"
SESSION = "932b93d4-360c-4529-87ee-75d3a6d89557"
AGENT_A = "a3d9c94df3116b37f"
AGENT_B = "b44e1c0a99d2f7e10"
ZONE_MAP = {
    "task": TASK, "frozen_at": "2026-10-12T10:00:00",
    "zones": {"A": ["app/a/**", "app/shared_a.py"], "B": ["app/b/**"]},
    "shared": [".claude/settings.json", ".agentry/pipeline.json", ".agentry/tasks/**"],
}


class ZoneGateCase(unittest.TestCase):
    """A sandbox project with a registered run, a frozen map and agent transcripts."""

    stage = "implement"

    def setUp(self):
        base = tmproot.sandbox(self, "zgate_")
        self.root = base / "proj"
        self.transcripts = base / "claude-projects"
        for rel in (".agentry/state", ".agentry/tasks/swarm/" + TASK, "app/a", "app/b", "app/c",
                    "tmp", "docs"):
            (self.root / rel).mkdir(parents=True)
        (self.root / ".agentry" / "pipeline.json").write_text("{}", encoding="utf-8")
        for rel in ("app/a/x.py", "app/b/x.py", "app/c/x.py", "app/shared_a.py"):
            (self.root / rel).write_text("pass\n", encoding="utf-8")
        state_dir = self.root / ".agentry" / "state"
        for name, value in (("ROOT", self.root), ("STATE_DIR", state_dir),
                            ("DB_PATH", state_dir / "run.db"),
                            ("PIPELINE_PATH", self.root / ".agentry" / "pipeline.json")):
            patcher = mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cwd = str(self.root)
        self.set_stage(self.stage)
        self.freeze(ZONE_MAP)
        self.agent(AGENT_A, "Swarm zone: A\nTask: build the thing")
        self.agent(AGENT_B, "Swarm zone: B\nTask: build the other thing")

    # --- fixtures ---

    def set_stage(self, stage):
        conn = state.connect()
        try:
            if state.get_run(conn, TASK):
                state.set_fields(conn, TASK, stage=stage)
            else:
                state.create_run(conn, TASK, "bugfix", stage)
            conn.commit()
        finally:
            conn.close()

    def freeze(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        zones.zones_path().write_text(text, encoding="utf-8")

    @property
    def parent_transcript(self) -> Path:
        return self.transcripts / "proj" / f"{SESSION}.jsonl"

    def agent(self, agent_id, prompt):
        """The agent's own transcript: line 1 is the dispatch prompt."""
        sub = self.parent_transcript.parent / SESSION / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        line = {"isSidechain": True, "type": "user", "message": {"role": "user", "content": prompt}}
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    def payload(self, agent_id=AGENT_A, **over):
        data = {"session_id": SESSION, "transcript_path": str(self.parent_transcript),
                "cwd": self.cwd, "agent_id": agent_id, "agent_type": "senior-backend-dev"}
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    # --- calls ---

    def run_dev(self, tool, ti, cwd=None, payload="default"):
        payload = self.payload() if payload == "default" else payload
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = agent_gate.handle_dev(tool, ti, cwd=cwd if cwd is not None else self.cwd,
                                         payload=payload)
        return code, err.getvalue()

    def write(self, rel, tool="Write", **kw):
        path = rel if Path(rel).is_absolute() else str(self.root / rel)
        field = "content" if tool == "Write" else "new_string"
        return self.run_dev(tool, {"file_path": path, field: "x"}, **kw)

    def bash(self, command, **kw):
        return self.run_dev("Bash", {"command": command}, **kw)

    def assertAllowed(self, result):
        self.assertEqual(0, result[0], result[1])

    def assertZoneDenied(self, result, *needles):
        code, err = result
        self.assertEqual(2, code, err)
        for needle in needles:
            self.assertIn(needle, err)


class OwnZoneAndSharedTest(ZoneGateCase):
    def test_write_inside_own_zone_is_allowed(self):
        self.assertAllowed(self.write("app/a/new.py"))
        self.assertAllowed(self.write("app/a/deep/er/new.py"))

    def test_exact_file_named_by_the_zone_is_allowed(self):
        self.assertAllowed(self.write("app/shared_a.py"))

    def test_each_writing_tool_is_judged_by_the_same_rule(self):
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.write("app/a/x.py", tool))
                self.assertZoneDenied(self.write("app/b/x.py", tool), "zone B")
        multi = {"file_path": str(self.root / "app/b/x.py"), "edits": [{"new_string": "x"}]}
        self.assertZoneDenied(self.run_dev("MultiEdit", multi), "zone B")
        multi["file_path"] = str(self.root / "app/a/x.py")
        self.assertAllowed(self.run_dev("MultiEdit", multi))
        nb = {"notebook_path": str(self.root / "app/b/n.ipynb"), "new_source": "x"}
        self.assertZoneDenied(self.run_dev("NotebookEdit", nb), "zone B")

    def test_shared_tmp_directory_is_allowed_from_any_zone(self):
        for agent_id in (AGENT_A, AGENT_B):
            with self.subTest(agent=agent_id):
                self.assertAllowed(self.write("tmp/log.txt", payload=self.payload(agent_id)))
                self.assertAllowed(self.bash("echo x > tmp/out.log",
                                             payload=self.payload(agent_id)))

    def test_relative_path_resolves_against_the_payload_cwd(self):
        self.assertAllowed(self.run_dev("Write", {"file_path": "x.py", "content": "x"},
                                        cwd=str(self.root / "app" / "a")))
        self.assertZoneDenied(self.run_dev("Write", {"file_path": "x.py", "content": "x"},
                                           cwd=str(self.root / "app" / "b")), "zone B")


class OutsideZoneTest(ZoneGateCase):
    def test_write_into_a_sibling_zone_is_denied_and_names_the_owner(self):
        self.assertZoneDenied(self.write("app/b/new.py"), "zone A", "belongs to zone B",
                              "new.py")
        code, err = self.write("app/a/new.py", payload=self.payload(AGENT_B))
        self.assertEqual(2, code)
        self.assertIn("belongs to zone A", err)

    def test_undeclared_path_is_denied_with_no_owner(self):
        self.assertZoneDenied(self.write("app/c/x.py"), "no zone owns it")
        self.assertZoneDenied(self.write("docs/readme.md"), "no zone owns it")

    def test_shared_files_that_no_zone_names_are_denied(self):
        for rel in ("package.json", "composer.json"):
            with self.subTest(rel=rel):
                self.assertZoneDenied(self.write(rel), "no zone owns it")

    def test_dotdot_traversal_is_judged_where_it_lands(self):
        self.assertZoneDenied(self.write("app/a/../b/x.py"), "belongs to zone B")

    def test_path_case_does_not_change_the_owner(self):
        self.assertZoneDenied(self.write("APP/B/x.py"), "belongs to zone B")

    def test_a_directory_prefix_sibling_is_not_inside_the_zone(self):
        (self.root / "app" / "a2").mkdir()
        self.assertZoneDenied(self.write("app/a2/x.py"), "no zone owns it")

    def test_path_outside_the_project_is_denied(self):
        other = self.root.parent / "elsewhere" / "x.py"
        self.assertZoneDenied(self.write(str(other)), "outside the project")

    def test_deny_names_the_zone_not_the_agent_type(self):
        code, err = self.write("app/b/x.py")
        self.assertEqual(2, code)
        self.assertNotIn("senior-backend-dev", err)


class SwarmStateDirTest(ZoneGateCase):
    """A zoned agent posts to the board and writes QA reports in ITS task's swarm dir,
    and nowhere else under .agentry/tasks."""

    SWARM = ".agentry/tasks/swarm/" + TASK

    def test_zoned_agent_may_write_the_board_and_qa_reports(self):
        for agent_id in (AGENT_A, AGENT_B):
            payload = self.payload(agent_id)
            for name in ("board.md", "qa-x.md", "e2e/shot.txt"):
                with self.subTest(agent=agent_id, name=name):
                    self.assertAllowed(self.write(f"{self.SWARM}/{name}", payload=payload))
                    self.assertAllowed(self.write(f"{self.SWARM}/{name}", "Edit", payload=payload))

    def test_zoned_agent_may_append_to_the_board_from_bash(self):
        self.assertAllowed(self.bash(f"echo note >> {self.SWARM}/board.md"))
        self.assertAllowed(self.bash(f"tee {self.SWARM}/qa-x.md"))
        self.assertAllowed(self.bash(f"cd {self.SWARM} && rm qa-x.md"))

    def test_another_tasks_swarm_dir_is_denied(self):
        for rel in (".agentry/tasks/swarm/task-0121/board.md", ".agentry/tasks/swarm/board.md"):
            with self.subTest(rel=rel):
                self.assertZoneDenied(self.write(rel), "no zone owns it")

    def test_the_rest_of_agentry_tasks_stays_denied(self):
        for rel in (".agentry/tasks/backlog/task-0121.md", ".agentry/tasks/active/task-0120.md",
                    ".agentry/tasks/done/task-0100.md", ".agentry/tasks/handoffs/task-0118.md",
                    ".agentry/tasks/epics/epic-0002.md"):
            with self.subTest(rel=rel):
                self.assertZoneDenied(self.write(rel), "no zone owns it")
                self.assertZoneDenied(self.bash(f"echo x > {rel}"), "no zone owns it")

    def test_dotdot_out_of_the_swarm_dir_is_judged_where_it_lands(self):
        self.assertZoneDenied(self.write(f"{self.SWARM}/../../backlog/task-0121.md"),
                              "no zone owns it")
        self.assertZoneDenied(self.write(f"{self.SWARM}/../task-0121/board.md"),
                              "no zone owns it")

    def test_swarm_dir_is_resolved_under_the_workspace_root(self):
        self.assertEqual(self.root / ".agentry" / "tasks" / "swarm" / TASK,
                         zones.swarm_dir(TASK))

    def test_without_a_zone_line_the_swarm_dir_is_still_closed(self):
        self.agent("a0000000000000001", "Task only, no zone line")
        payload = self.payload("a0000000000000001")
        self.assertZoneDenied(self.write(f"{self.SWARM}/board.md", payload=payload), "names no")

    def test_non_zoned_runs_are_unchanged(self):
        zones.zones_path().unlink()
        for rel in (f"{self.SWARM}/board.md", ".agentry/tasks/backlog/task-0121.md",
                    ".agentry/tasks/done/task-0100.md"):
            with self.subTest(rel=rel):
                self.assertAllowed(self.write(rel))
        zones.zones_path().write_text(json.dumps(ZONE_MAP), encoding="utf-8")
        self.set_stage("ready")
        self.assertAllowed(self.write(".agentry/tasks/done/task-0100.md"))


class BashWriteTest(ZoneGateCase):
    def test_redirect_into_own_zone_passes_and_into_another_denies(self):
        self.assertAllowed(self.bash("echo x > app/a/out.txt"))
        self.assertZoneDenied(self.bash("echo x > app/b/out.txt"), "belongs to zone B")
        self.assertZoneDenied(self.bash("echo x >> app/b/out.txt"), "belongs to zone B")

    def test_mutating_commands_are_judged_by_the_files_they_name(self):
        allowed = ("rm app/a/x.py", "mkdir -p app/a/new",
                   "chmod +x app/a/x.py", "chmod -R 755 app/a", "cp app/b/x.py app/a/copy.py",
                   "cp -t app/a app/b/x.py", "mv app/a/x.py app/a/y.py",
                   "tee app/a/t.txt", "sed -i 's/a/b/' app/a/x.py",
                   "sed -i -e 's/a/b/' app/a/x.py", "rm -rf tmp/scratch")
        for command in allowed:
            with self.subTest(command=command):
                self.assertAllowed(self.bash(command))
        denied = ("rm app/b/x.py", "mkdir app/b/new", "chmod +x app/b/x.py",
                  "cp app/a/x.py app/b/copy.py", "cp -t app/b app/a/x.py",
                  "mv app/a/x.py app/b/x.py", "mv app/b/x.py app/a/x.py",
                  "tee app/b/t.txt", "sed -i 's/a/b/' app/b/x.py",
                  "sed -i -e 's/a/b/' app/b/x.py", "rm app/a/x.py app/b/x.py")
        for command in denied:
            with self.subTest(command=command):
                self.assertZoneDenied(self.bash(command), "zone B")

    def test_cd_into_another_zone_moves_the_relative_target(self):
        self.assertZoneDenied(self.bash("cd app/b && echo x > y.txt"), "belongs to zone B")
        self.assertZoneDenied(self.bash("cd app/b && rm x.py"), "belongs to zone B")
        self.assertAllowed(self.bash("cd app/a && rm x.py"))

    def test_glob_operand_is_expanded_before_judging(self):
        self.assertAllowed(self.bash("rm app/a/*.py"))
        self.assertZoneDenied(self.bash("rm app/b/*.py"), "zone B")

    def test_nested_shell_body_is_judged(self):
        self.assertZoneDenied(self.bash('bash -c "echo x > app/b/y.txt"'), "belongs to zone B")

    def test_commands_that_write_no_named_file_are_not_zone_business(self):
        for command in ("ls app/b", "cat app/b/x.py", "git status", "git diff --stat",
                        "git log --oneline -3", "python -m unittest tests.test_x -k one",
                        "npm install left-pad", "pip install requests",
                        "echo hi 2>&1", "grep -rn zone app/b"):
            with self.subTest(command=command):
                self.assertAllowed(self.bash(command))

    def test_each_zone_still_goes_through_the_protected_state_check_first(self):
        code, err = self.bash("echo auto > .agentry/state/approvals")
        self.assertEqual(2, code)
        self.assertIn("agentry/state", err)
        self.assertNotIn("swarm zone", err)


class MissingIdentityTest(ZoneGateCase):
    def test_agent_without_a_zone_line_may_not_write(self):
        self.agent("a0000000000000001", "Task only, no zone line")
        payload = self.payload("a0000000000000001")
        self.assertZoneDenied(self.write("app/a/x.py", payload=payload), "names no")
        self.assertZoneDenied(self.write("tmp/x.log", payload=payload), "names no")
        self.assertZoneDenied(self.bash("echo x > app/a/o.txt", payload=payload), "names no")

    def test_agent_without_a_zone_line_may_still_read(self):
        self.agent("a0000000000000001", "Task only, no zone line")
        payload = self.payload("a0000000000000001")
        self.assertAllowed(self.bash("ls app/a", payload=payload))
        self.assertAllowed(self.bash("git status", payload=payload))

    def test_agent_with_two_different_zone_lines_is_unzoned(self):
        self.agent("a0000000000000002", "Swarm zone: A\nSwarm zone: B\n")
        self.assertZoneDenied(self.write("app/a/x.py", payload=self.payload("a0000000000000002")),
                              "names no")

    def test_agent_transcript_missing_is_unzoned(self):
        self.assertZoneDenied(self.write("app/a/x.py", payload=self.payload("a0000000000000003")),
                              "names no")

    def test_payload_without_agent_id_session_or_transcript_is_unzoned(self):
        for missing in ("agent_id", "session_id", "transcript_path"):
            with self.subTest(missing=missing):
                payload = self.payload(**{missing: None})
                self.assertZoneDenied(self.write("app/a/x.py", payload=payload), "names no")

    def test_session_id_cannot_walk_out_of_the_transcript_dir(self):
        evil = self.transcripts / "evil" / "subagents"
        evil.mkdir(parents=True)
        line = {"message": {"role": "user", "content": "Swarm zone: A"}}
        (evil / f"agent-{AGENT_A}.jsonl").write_text(json.dumps(line), encoding="utf-8")
        payload = self.payload(session_id="../evil")
        self.assertZoneDenied(self.write("app/a/x.py", payload=payload), "names no")

    def test_parent_transcript_naming_another_zone_is_ignored(self):
        parent = [{"type": "user", "message": {"role": "user",
                                               "content": "Swarm zone: B\nSwarm zone: A"}},
                  {"type": "assistant", "message": {"role": "assistant", "content": [
                      {"type": "tool_use", "input": {"prompt": "Swarm zone: B\nbuild"}}]}}]
        self.parent_transcript.write_text("\n".join(json.dumps(r) for r in parent) + "\n",
                                          encoding="utf-8")
        self.assertAllowed(self.write("app/a/x.py"))
        self.assertZoneDenied(self.write("app/b/x.py"), "belongs to zone B")

    def test_parent_transcript_alone_never_grants_a_zone(self):
        parent = {"type": "user", "message": {"role": "user", "content": "Swarm zone: A"}}
        self.parent_transcript.write_text(json.dumps(parent) + "\n", encoding="utf-8")
        payload = self.payload("a0000000000000004")
        self.assertZoneDenied(self.write("app/a/x.py", payload=payload), "names no")


class NoZonesInPlayTest(ZoneGateCase):
    def test_no_zones_file_leaves_dev_exactly_as_before(self):
        zones.zones_path().unlink()
        for payload in (self.payload(), self.payload(agent_id=None), {}):
            with self.subTest(payload=sorted(payload)):
                self.assertAllowed(self.write("app/b/x.py", payload=payload))
                self.assertAllowed(self.write("docs/readme.md", payload=payload))
                self.assertAllowed(self.bash("echo x > app/b/o.txt", payload=payload))
                self.assertAllowed(self.bash("git stash list", payload=payload))

    def test_map_of_a_task_outside_implement_and_test_is_not_in_force(self):
        for stage in ("ready", "done", "plan"):
            self.set_stage(stage)
            with self.subTest(stage=stage):
                self.assertAllowed(self.write("app/b/x.py", payload=self.payload(agent_id=None)))
                self.assertAllowed(self.bash("echo x > app/c/o.txt"))

    def test_map_stays_in_force_at_the_test_stage(self):
        self.set_stage("test")
        self.assertZoneDenied(self.write("app/b/x.py"), "zone B")

    def test_run_row_gone_means_not_in_force(self):
        state.DB_PATH.unlink()
        self.assertAllowed(self.write("app/b/x.py"))

    def test_a_direct_call_without_a_payload_skips_the_zone_check(self):
        self.assertAllowed(self.write("app/b/x.py", payload=None))

    def test_tools_that_are_not_writes_are_not_checked(self):
        self.assertAllowed(self.run_dev("Read", {"file_path": str(self.root / "app/b/x.py")}))


class UnusableMapTest(ZoneGateCase):
    def test_corrupt_map_denies_writes(self):
        for body in ("not json", "[]", "{}", '{"task": "task-0120"}',
                     '{"task": "task-0120", "zones": []}',
                     '{"task": 7, "zones": {"A": ["app/a/**"]}}',
                     '{"task": "task-0120", "zones": {"A": "app/a/**"}}',
                     '{"task": "task-0120", "zones": {"A": []}}',
                     '{"task": "task-0120", "zones": {"A": ["../escape"]}}',
                     '{"task": "task-0120", "zones": {"A": [3]}}'):
            self.freeze(body)
            with self.subTest(body=body):
                self.assertZoneDenied(self.write("app/a/x.py"), "cannot be used")
                self.assertZoneDenied(self.bash("echo x > app/a/o.txt"), "cannot be used")

    def test_corrupt_map_does_not_deny_reads(self):
        self.freeze("not json")
        self.assertAllowed(self.bash("ls app/a"))

    def test_a_check_that_crashes_denies_instead_of_allowing(self):
        with mock.patch.object(agent_gate, "agent_zone", side_effect=RuntimeError("boom")):
            self.assertZoneDenied(self.write("app/a/x.py"), "the check itself failed")

    def test_dormant_map_with_a_valid_shape_is_not_a_deny(self):
        self.set_stage("ready")
        self.assertAllowed(self.write("app/c/x.py"))

    def test_prove_then_gate_end_to_end(self):
        board = zones.board_path(TASK)
        board.write_text("# b\n\n## Zones\n\n| zone | paths |\n|---|---|\n"
                         "| A | app/a/** |\n| B | app/b/** |\n", encoding="utf-8")
        ok, lines = zones.prove(TASK)
        self.assertTrue(ok, lines)
        self.assertAllowed(self.write("app/a/x.py"))
        self.assertZoneDenied(self.write("app/b/x.py"), "belongs to zone B")


class GitMutatorTest(ZoneGateCase):
    MUTATORS = ("git checkout main", "git checkout -- app/a/x.py", "git checkout -b other",
                "git switch main", "git stash", "git stash pop", "git stash push -u",
                "git reset --hard", "git reset HEAD~1", "git restore app/b/x.py",
                "git restore --staged .", "git clean -fd", "git apply fix.patch",
                "git merge other", "git rebase main", "git pull", "git rm app/b/x.py",
                "git mv app/a/x.py app/b/x.py", "git cherry-pick abc", "git revert abc")

    def test_zoned_agent_may_not_run_the_tree_rewriting_git_commands(self):
        for command in self.MUTATORS:
            with self.subTest(command=command):
                code, err = self.bash(command)
                self.assertEqual(2, code, command)
                self.assertIn("swarm zone map is in force", err)

    def test_spelling_variants_are_denied_too(self):
        for command in ("git -C app/a checkout main", "git -c core.x=1 reset --hard",
                        "cd app/a && git stash", "git status; git clean -fd",
                        'bash -c "git reset --hard"', "git.exe checkout main",
                        "echo $(git stash)"):
            with self.subTest(command=command):
                self.assertZoneDenied(self.bash(command), "swarm zone map is in force")

    def test_unzoned_agent_is_denied_them_as_well(self):
        payload = self.payload(agent_id=None)
        self.assertZoneDenied(self.bash("git checkout main", payload=payload), "git checkout")

    def test_read_only_git_stays_allowed(self):
        for command in ("git status", "git diff", "git diff --stat HEAD", "git log -5",
                        "git show HEAD", "git branch --show-current", "git rev-parse HEAD",
                        "git merge-base --is-ancestor a b", "git stash list",
                        "git diff HEAD -- checkout.py", "git log --grep reset"):
            with self.subTest(command=command):
                self.assertNotIn("swarm zone map", self.bash(command)[1], command)

    def test_other_zone_agent_is_denied_the_same_commands(self):
        self.assertZoneDenied(self.bash("git checkout main", payload=self.payload(AGENT_B)),
                              "swarm zone map is in force")

    def test_git_commands_are_free_when_no_map_is_in_force(self):
        zones.zones_path().unlink()
        code, err = self.bash("git stash list")
        self.assertEqual(0, code, err)
        code, err = self.bash("git checkout --help")
        self.assertNotIn("swarm zone map", err)


class GitMutatesSubsTest(unittest.TestCase):
    """git_mutates keeps its old answer without `subs` and answers the zone question with it."""

    def test_default_question_is_unchanged(self):
        self.assertEqual("git commit", agent_gate.git_mutates("git commit -m x"))
        self.assertEqual("", agent_gate.git_mutates("git checkout main"))
        self.assertEqual("git checkout --", agent_gate.git_mutates("git checkout -- a"))
        self.assertEqual("", agent_gate.git_mutates("git stash"))

    def test_subs_replaces_the_set(self):
        subs = agent_gate.ZONED_GIT_DENIED
        self.assertEqual("git checkout", agent_gate.git_mutates("git checkout main", subs=subs))
        self.assertEqual("git stash", agent_gate.git_mutates("git stash", subs=subs))
        self.assertEqual("", agent_gate.git_mutates("git commit -m x", subs=subs))
        self.assertEqual("", agent_gate.git_mutates("git diff HEAD -- add.py", subs=subs))
        self.assertEqual("git reset", agent_gate.git_mutates('sh -c "git reset --hard"', subs=subs))


class EvaluatePlumbingTest(ZoneGateCase):
    """The hook entry point hands the payload to the dev handler and to nobody else."""

    def evaluate(self, profile, tool, ti, payload):
        err = io.StringIO()
        with (mock.patch.object(sys, "argv", ["agent_gate.py", "--profile", profile]),
              mock.patch.object(pretool_gate, "read_payload",
                                return_value={"tool_name": tool, "tool_input": ti, **payload}),
              contextlib.redirect_stderr(err)):
            return agent_gate.evaluate(), err.getvalue()

    def test_dev_hook_enforces_the_zone_from_the_real_payload_keys(self):
        own = {"file_path": str(self.root / "app/a/x.py"), "content": "x"}
        other = {"file_path": str(self.root / "app/b/x.py"), "content": "x"}
        self.assertEqual(0, self.evaluate("dev", "Write", own, self.payload())[0])
        code, err = self.evaluate("dev", "Write", other, self.payload())
        self.assertEqual(2, code)
        self.assertIn("belongs to zone B", err)

    def test_main_thread_payload_without_agent_id_is_unzoned(self):
        other = {"file_path": str(self.root / "app/a/x.py"), "content": "x"}
        code, err = self.evaluate("dev", "Write", other, self.payload(agent_id=None))
        self.assertEqual(2, code)
        self.assertIn("names no", err)

    def test_readonly_and_docs_profiles_are_not_zone_gated(self):
        doc = {"file_path": str(self.root / "docs/readme.md"), "content": "x"}
        self.assertEqual(0, self.evaluate("docs", "Write", doc, self.payload())[0])
        code, _ = self.evaluate("readonly", "Bash", {"command": "ls app/b"}, self.payload())
        self.assertEqual(0, code)


if __name__ == "__main__":
    unittest.main()

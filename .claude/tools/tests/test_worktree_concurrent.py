"""epic-0004 / task-0132: two real tasks in two real worktrees, side by side.

The proof the unit modules cannot give. A sandbox workspace holds a copy of the harness
tools, a real git repo under `src/` and a pipeline.json with worktrees enabled and two
slots. Every step is a separate child process: the real advance.py and approve.py, the
real gate.py, and the real pretool_gate.py and agent_gate.py fed hook payloads on stdin.
Nothing about git, sqlite or the gates is mocked.

The story, in order:

  * both tasks register (own branch, own tree, `src/` stays on main and clean);
  * the plan gate is per tree: one task's unapproved plan does not freeze the other's tree;
  * both implement exit gates run at the same moment, and a file barrier makes that
    provable: neither gate can finish until the other has started. Each sees only its own
    branch and its own files;
  * the review freeze is per tree, a commit is judged by the tree it runs in, the trunk
    checkout takes no edits;
  * A merges; B is refused for freshness, syncs with main, is re-gated and merges;
  * both trees are gone, both branches are kept, both runs are done, both files are on main.

A second case pins the other freshness check, the one advance.py makes on the way into
the commit checkpoint.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling import resolves)

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import tmproot

TOOLS_DIR = Path(__file__).resolve().parents[1]
ALLOW, DENY = 0, 2  # a hook's exit code: 0 lets the call through, 2 refuses it
WAIT = 300  # seconds any one child may take before the test gives up on it
A, B, C = "task-0201", "task-0202", "task-0203"
FILE_A, FILE_B = "feature_a.txt", "feature_b.txt"
STALE = "main moved {n} commits: `git merge main` in the worktree"

# The exit gate every stage command shares. It runs in the task's tree (that is what is
# under test), notes where it ran, which branch was checked out and which files it could
# see, and - with --barrier - refuses to finish until a second gate of the same stage has
# started. Two gates can only both get past the barrier if their lifetimes overlap.
GATE_SCRIPT = '''\
import json
import os
import subprocess
import sys
import time
from pathlib import Path

task, stage = sys.argv[1], sys.argv[2]
box = Path(__file__).resolve().parents[2] / "barrier"
box.mkdir(exist_ok=True)
here = Path.cwd()
head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
seen = {"task": task, "stage": stage, "pid": os.getpid(), "cwd": str(here), "branch": head,
        "files": sorted(p.name for p in here.iterdir() if p.is_file()), "started": time.time()}
(box / f"{task}.{stage}.in").write_text("here", encoding="utf-8")
if "--barrier" in sys.argv:
    give_up = time.time() + 120
    while len(list(box.glob(f"*.{stage}.in"))) < 2:
        if time.time() > give_up:
            sys.exit(3)
        time.sleep(0.05)
seen["released"] = time.time()
(box / f"{task}.{stage}.json").write_text(json.dumps(seen), encoding="utf-8")
print(json.dumps(seen))
sys.exit(0 if head.endswith("/" + task) else 1)
'''


class Job:
    """A running child whose output goes to files, never to pipes."""

    def __init__(self, proc: subprocess.Popen, out: Path, err: Path):
        self.proc, self.out, self.err = proc, out, err

    def finish(self) -> tuple[int, str, str]:
        try:
            code = self.proc.wait(timeout=WAIT)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise AssertionError(f"child did not finish in {WAIT}s: {self.proc.args}") from None
        return (code, self.out.read_text(encoding="utf-8", errors="replace"),
                self.err.read_text(encoding="utf-8", errors="replace"))


class WorkspaceCase(unittest.TestCase):
    """The sandbox workspace and the helpers that drive it from the outside."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "e2e_").resolve()
        self.src = self.root / "src"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.calls = 0
        shutil.copytree(TOOLS_DIR, self.root / ".claude" / "tools",
                        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        (self.root / ".claude" / "tools" / "e2e_gate.py").write_text(GATE_SCRIPT, encoding="utf-8")
        for folder in ("backlog", "active", "done"):
            (self.root / ".agentry" / "tasks" / folder).mkdir(parents=True)
        (self.root / ".agentry" / "plans").mkdir()
        self.src.mkdir()
        self.git(self.src, "init", "-b", "main")
        self.git(self.src, "config", "user.email", "e2e@example.com")
        self.git(self.src, "config", "user.name", "E2E")
        (self.src / ".gitignore").write_text("/tmp/\n__pycache__/\n", encoding="utf-8")
        (self.src / "README.md").write_text("hello\n", encoding="utf-8")
        self.git(self.src, "add", ".")
        self.git(self.src, "commit", "-m", "first")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PIPELINE_LANE", "AGENTRY_UNATTENDED")}
        self.env["CLAUDE_PROJECT_DIR"] = str(self.root)
        self.env["AGENTRY_NO_CONSOLE_INJECT"] = "1"
        self.write_config(barrier=False)

    # --- the sandbox --------------------------------------------------------------

    def write_config(self, barrier: bool) -> None:
        def gate(stage: str, extra: str = "") -> dict:
            return {"cmd": "python {root}/.claude/tools/e2e_gate.py {task} " + stage + extra,
                    "expect_exit": 0}

        build = {
            "editing_stages": ["plan", "implement", "test", "review"],
            "stages": [
                {"name": "plan", "owner": "dev", "skip_for_types": ["bugfix", "techdebt"],
                 "exit_gate": {"cmd": "python {root}/.claude/tools/pipeline/artifact_gate.py "
                                      "--task {task} --kind plan", "expect_exit": 0}},
                {"name": "plan-approval", "owner": "ceo", "checkpoints": ["plan"],
                 "skip_for_types": ["bugfix", "techdebt"]},
                {"name": "implement", "owner": "dev",
                 "exit_gate": gate("implement", " --barrier" if barrier else "")},
                {"name": "test", "owner": "qa-engineer", "exit_gate": gate("test")},
                {"name": "review", "owner": "reviewer", "exit_gate": gate("review")},
                {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"],
                 "auto_approve": []},
                {"name": "done", "owner": "ceo", "terminal": True}]}
        plan = {"editing_stages": ["formalize"], "stages": [
            {"name": "formalize", "owner": "architect"}, {"name": "done", "owner": "ceo", "terminal": True}]}
        config = {
            "cwd": "src", "retry_budget": 3, "continuation_ceiling": 30, "main_branch": "main",
            "protected_branches": ["staging", "production"], "branch_pattern": "{type}/{task}",
            "approvals_never_granted": ["push", "trunk_push"],
            "repos": {"agentry": {"path": "src"}},
            "workflow": {"mode": "solo", "push_needs_approval": True},
            "handoff": {"enabled": False}, "memory": {"enabled": False},
            "worktrees": {"enabled": True, "max_parallel": 2},
            "pipelines": {"build": build, "plan": plan}}
        (self.root / ".agentry" / "pipeline.json").write_text(json.dumps(config), encoding="utf-8")

    def task_file(self, task: str) -> None:
        (self.root / ".agentry" / "tasks" / "backlog" / f"{task}.md").write_text(
            f"---\nid: {task[-4:]}\ntype: feature\npriority: high\ntitle: {task}\nspec: none\n"
            f"assignee: senior-backend-dev\ncompleted:\n---\n## Acceptance Criteria\n- [ ] one\n",
            encoding="utf-8")

    def plan_file(self, task: str) -> None:
        (self.root / ".agentry" / "plans" / f"2026-10-13-e2e-{task}.md").write_text(
            f"# Plan for {task}\n\nThis plan belongs to {task}. It exists so the plan exit gate has "
            f"a real document to read: the gate wants the task id in the file name, the task id in "
            f"the text and at least four hundred characters of substance, so the paragraph goes on "
            f"for a while about nothing in particular, the way a short plan does when a test wrote "
            f"it, and ends only once the length floor is cleared by a comfortable margin.\n",
            encoding="utf-8")

    def tree(self, task: str) -> Path:
        return self.root / "src-wt" / task

    # --- processes ----------------------------------------------------------------

    def git(self, repo: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)} in {repo}: {done.stderr}")
        return done.stdout.strip()

    def spawn(self, argv: list[str], payload: dict | None = None) -> Job:
        self.calls += 1
        base = self.logs / f"{self.calls:03d}"
        out, err = base.with_suffix(".out"), base.with_suffix(".err")
        stdin = subprocess.DEVNULL
        if payload is not None:
            feed = base.with_suffix(".in")
            feed.write_text(json.dumps(payload), encoding="utf-8")
            stdin = feed.open("rb")
        with out.open("wb") as sink, err.open("wb") as errors:
            proc = subprocess.Popen([sys.executable, *argv], cwd=self.root, env=self.env,
                                    stdin=stdin, stdout=sink, stderr=errors)
        if payload is not None:
            stdin.close()
        return Job(proc, out, err)

    def script(self, name: str) -> str:
        return str(self.root / ".claude" / "tools" / "pipeline" / name)

    def advance_job(self, task: str, *extra: str) -> Job:
        return self.spawn([self.script("advance.py"), "--task", task, *extra])

    @staticmethod
    def answer(job: Job) -> dict:
        code, out, err = job.finish()
        assert code == 0, f"exit {code}\n{out}\n{err}"
        return json.loads(out)

    def advance(self, task: str, *extra: str) -> dict:
        return self.answer(self.advance_job(task, *extra))

    def approve(self, task: str, gate: str) -> None:
        reply = self.answer(self.spawn([self.script("approve.py"), "--task", task, "--gate", gate]))
        self.assertTrue(reply["ok"], reply)

    def gate_cli(self, task: str, stage: str) -> dict:
        code, out, err = self.spawn([self.script("gate.py"), "--task", task, "--stage", stage]).finish()
        self.assertEqual(0, code, f"{out}\n{err}")
        return json.loads(out)

    def hook(self, script: str, payload: dict, *args: str) -> tuple[int, str]:
        code, _, err = self.spawn([self.script(script), *args], payload).finish()
        return code, err

    def orchestrator(self, command: str) -> tuple[int, str]:
        """The main thread's Bash call, judged by pretool_gate.py."""
        return self.hook("pretool_gate.py", self.payload("Bash", command=command))

    def dev_agent(self, tool: str, **tool_input) -> tuple[int, str]:
        """A dev subagent's call, judged by agent_gate.py --profile dev."""
        return self.hook("agent_gate.py", self.payload(tool, agent=True, **tool_input), "--profile", "dev")

    def payload(self, tool: str, agent: bool = False, **tool_input) -> dict:
        body = {"tool_name": tool, "tool_input": tool_input, "cwd": str(self.root)}
        if agent:
            body["agent_type"] = "senior-backend-dev"
            body.update(self.agent_identity(tool_input))
        return body

    def agent_identity(self, tool_input: dict) -> dict:
        """The payload fields that let agent_gate read the agent's dispatch prompt. The agent
        works for the task whose tree the call names (task A for a call that names neither)."""
        text = " ".join(str(v) for v in tool_input.values())
        task = B if f"src-wt/{B}" in text.replace("\\", "/") else A
        agent_id = "a" + task.rsplit("-", 1)[1]
        parent = self.root / "claude-projects" / "proj" / "e2e-session.jsonl"
        sub = parent.parent / "e2e-session" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        line = {"isSidechain": True, "type": "user",
                "message": {"role": "user", "content": f"Task worktree: {task}\nWork in the tree."}}
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
        return {"session_id": "e2e-session", "transcript_path": str(parent), "agent_id": agent_id}

    def edit(self, path: Path) -> tuple[int, str]:
        return self.dev_agent("Edit", file_path=str(path), old_string="old", new_string="new")

    # --- reading the sandbox ------------------------------------------------------

    def run_row(self, task: str) -> dict:
        conn = sqlite3.connect(self.root / ".agentry" / "state" / "run.db")
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM runs WHERE task = ?", (task,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else {}

    def seen(self, task: str, stage: str) -> dict:
        return json.loads((self.root / "barrier" / f"{task}.{stage}.json").read_text(encoding="utf-8"))

    def trees(self) -> list[str]:
        listing = self.git(self.src, "worktree", "list", "--porcelain")
        return [Path(line[len("worktree "):]).as_posix() for line in listing.splitlines()
                if line.startswith("worktree ")]

    def branch_exists(self, branch: str) -> bool:
        done = subprocess.run(["git", "-C", str(self.src), "rev-parse", "--verify", "--quiet",
                               f"refs/heads/{branch}"], capture_output=True, text=True)
        return done.returncode == 0

    # --- assertions ---------------------------------------------------------------

    def assertAllowed(self, verdict: tuple[int, str]) -> None:
        self.assertEqual(ALLOW, verdict[0], verdict[1])

    def assertDenied(self, verdict: tuple[int, str], *fragments: str) -> None:
        self.assertEqual(DENY, verdict[0], f"expected a deny, the hook allowed it: {verdict[1]}")
        for fragment in fragments:
            self.assertIn(fragment, verdict[1])

    def assertRanInTree(self, task: str, stage: str) -> dict:
        """The gate of `task` ran in its own tree and on its own branch. Returns what it saw."""
        seen = self.seen(task, stage)
        self.assertTrue(os.path.samefile(seen["cwd"], self.tree(task)), seen["cwd"])
        self.assertEqual(f"feature/{task}", seen["branch"])
        return seen

    def assertSeenOnly(self, task: str, stage: str, own: str, other: str) -> None:
        """...and among its own files, none of the other task's."""
        seen = self.assertRanInTree(task, stage)
        self.assertIn(own, seen["files"])
        self.assertNotIn(other, seen["files"])


class TwoTasksSideBySideTest(WorkspaceCase):

    def setUp(self):
        super().setUp()
        self.write_config(barrier=True)

    def test_two_tasks_run_side_by_side_and_merge_in_order(self):
        self.register_both()
        self.approve_plans_one_at_a_time()
        self.edit_in_separate_trees()
        self.implement_together()
        self.review_freeze_follows_the_tree()
        self.gate_the_rest_together()
        self.commits_are_judged_by_their_tree()
        self.merge_a_then_b_is_stale()
        self.sync_regate_and_merge_b()
        self.nothing_is_left_behind()

    # --- 1. registration ----------------------------------------------------------

    def register_both(self):
        for task in (A, B):
            self.task_file(task)
            self.plan_file(task)
        for task in (A, B):
            out = self.advance(task)
            self.assertEqual("started", out["action"], out)
            self.assertTrue(out["message"].endswith(f'(branch feature/{task}). Begin every Bash call with '
                                                    f'cd "{self.tree(task).as_posix()}", give Edit and Write '
                                                    f'absolute paths under it, and do not touch src/. '
                                                    f'Make the first line of the dispatch prompt exactly '
                                                    f'"Task worktree: {task}" (alone on that line), or the '
                                                    f"write gate refuses this agent's writes."),
                            out["message"])
            self.assertTrue((self.tree(task) / ".git").is_file())
            self.assertEqual(f"src-wt/{task}", self.run_row(task)["worktree"])
        self.assertEqual(3, len(self.trees()), self.trees())
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))
        trunk = self.git(self.src, "rev-parse", "main")
        for task in (A, B):
            self.assertEqual(trunk, self.git(self.src, "rev-parse", f"feature/{task}"))

    # --- 2. the plan gate is per tree ---------------------------------------------

    def approve_plans_one_at_a_time(self):
        for task in (A, B):
            self.assertEqual("advanced", self.advance(task)["action"])  # plan -> plan-approval
            parked = self.advance(task)
            self.assertEqual(("park", "plan"), (parked["action"], parked["awaiting_human"]), parked)
        # A run parked at plan-approval is not at an editing stage, so no `Task worktree:` line
        # is believed for it and the confinement refuses an edit in its tree.
        no_line = "no task worktree"
        self.assertDenied(self.edit(self.tree(A) / FILE_A), no_line)
        self.assertDenied(self.edit(self.tree(B) / FILE_B), no_line)

        self.approve(A, "plan")
        self.assertEqual("advanced", self.advance(A)["action"])
        self.assertEqual("implement", self.run_row(A)["stage"])
        self.assertAllowed(self.edit(self.tree(A) / FILE_A))
        self.assertDenied(self.edit(self.tree(B) / FILE_B), no_line)

        self.approve(B, "plan")
        self.assertEqual("advanced", self.advance(B)["action"])
        self.assertEqual("implement", self.run_row(B)["stage"])
        self.assertAllowed(self.edit(self.tree(B) / FILE_B))

    # --- 3. edits land in the tree, never in the trunk checkout -------------------

    def edit_in_separate_trees(self):
        self.assertDenied(self.dev_agent("Edit", file_path=str(self.src / "README.md"),
                                         old_string="hello", new_string="changed"), "repository checkout")
        self.assertDenied(self.dev_agent("Write", file_path=str(self.src / "stray.txt"), content="x\n"),
                          "repository checkout")
        self.assertDenied(self.dev_agent("Bash", command=f'cd "{self.src.as_posix()}" && echo x > stray.txt'),
                          "repository checkout")
        remove = f'git worktree remove --force "{self.tree(B).as_posix()}"'
        self.assertDenied(self.dev_agent("Bash", command=remove), "worktree")
        for task, name in ((A, FILE_A), (B, FILE_B)):
            target = self.tree(task) / name
            self.assertAllowed(self.dev_agent("Write", file_path=str(target), content=f"{task}\n"))
            target.write_text(f"{task}\n", encoding="utf-8")
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))
        self.assertFalse((self.src / FILE_A).exists() or (self.src / FILE_B).exists())

    # --- 4. two exit gates at the same moment -------------------------------------

    def implement_together(self):
        first, second = self.advance_job(A), self.advance_job(B)
        answers = [self.answer(first), self.answer(second)]
        for out in answers:
            self.assertEqual("advanced", out["action"], out)
            self.assertEqual("test", out["stage"])
        self.assertSeenOnly(A, "implement", FILE_A, FILE_B)
        self.assertSeenOnly(B, "implement", FILE_B, FILE_A)
        one, two = self.seen(A, "implement"), self.seen(B, "implement")
        self.assertNotEqual(one["pid"], two["pid"])
        # Each gate was released only after the other had started, so the two lifetimes overlap.
        # The clock is Windows' (about 16 ms), hence <=: the barrier is the proof, this is its echo.
        self.assertLessEqual(one["started"], two["released"])
        self.assertLessEqual(two["started"], one["released"])

    # --- 5. the review freeze belongs to one tree ---------------------------------

    def review_freeze_follows_the_tree(self):
        self.assertEqual("review", self.advance(A)["stage"])
        self.assertDenied(self.edit(self.tree(A) / FILE_A), "read-only")
        self.assertAllowed(self.edit(self.tree(B) / FILE_B))
        self.assertEqual("test", self.run_row(B)["stage"])
        self.assertEqual("review", self.advance(B)["stage"])
        self.assertDenied(self.edit(self.tree(B) / FILE_B), "read-only")

    def gate_the_rest_together(self):
        first, second = self.advance_job(A), self.advance_job(B)
        for out in (self.answer(first), self.answer(second)):
            self.assertEqual("advanced", out["action"], out)
            self.assertEqual("ready", out["stage"])
        self.assertSeenOnly(A, "review", FILE_A, FILE_B)
        self.assertSeenOnly(B, "review", FILE_B, FILE_A)
        for task, own, other in ((A, FILE_A, FILE_B), (B, FILE_B, FILE_A)):
            parked = self.advance(task)
            self.assertEqual(("park", "commit"), (parked["action"], parked["awaiting_human"]), parked)
            self.assertIn(own, parked["message"])  # the CEO's diff is read from the task's own tree
            self.assertNotIn(other, parked["message"])

    # --- 6. commits ---------------------------------------------------------------

    def commits_are_judged_by_their_tree(self):
        def commit(task: str) -> str:
            return f'cd "{self.tree(task).as_posix()}" && git commit -m "[{task}] feat: work"'

        self.assertDenied(self.orchestrator(commit(A)), f"Commit for {A} is not approved")
        # At the commit checkpoint the run is not at an editing stage: an agent has no line to show.
        self.assertDenied(self.dev_agent("Bash", command=commit(A)), "no task worktree")
        self.assertDenied(self.orchestrator(f'git commit -m "[{A}] feat: work"'), "Not on a feature branch")
        in_trunk = f'cd "{self.src.as_posix()}" && git commit -m "[{A}] feat: work"'
        self.assertDenied(self.orchestrator(in_trunk), "Not on a feature branch")

        self.approve(A, "commit")
        self.assertAllowed(self.orchestrator(commit(A)))
        self.assertDenied(self.dev_agent("Bash", command=commit(A)), "no task worktree")
        # A's approval does not reach B's tree, by its own branch or by borrowing A's tag.
        self.assertDenied(self.orchestrator(commit(B)), f"Commit for {B} is not approved")
        borrowed = f'cd "{self.tree(B).as_posix()}" && git commit -m "[{A}] feat: work"'
        self.assertDenied(self.orchestrator(borrowed), f"tagged [{A}]")
        self.git(self.tree(A), "add", FILE_A)
        self.git(self.tree(A), "commit", "-m", f"[{A}] feat: add a")

        self.approve(B, "commit")
        self.assertAllowed(self.orchestrator(commit(B)))
        self.git(self.tree(B), "add", FILE_B)
        self.git(self.tree(B), "commit", "-m", f"[{B}] feat: add b")

        for task in (A, B):
            waiting = self.advance(task)
            self.assertEqual(("park", "merge"), (waiting["action"], waiting["awaiting_human"]), waiting)

    # --- 7. the solo merge, in order ----------------------------------------------

    def merge_command(self, task: str) -> str:
        return (f'cd "{self.src.as_posix()}" && git merge --no-ff -m "Merge branch \'feature/{task}\'" '
                f'feature/{task}')

    def merge_a_then_b_is_stale(self):
        self.assertAllowed(self.orchestrator(self.merge_command(A)))
        self.git(self.src, "merge", "--no-ff", "-m", f"Merge branch 'feature/{A}'", f"feature/{A}")
        done = self.advance(A)
        self.assertEqual("done", done["action"], done)
        self.assertIn("Merged into main", done["message"])
        self.assertIn("Worktree removed (the branch is kept)", done["message"])
        self.assertFalse(self.tree(A).exists())
        self.assertTrue(self.tree(B).is_dir())

        # A's own commit and its merge commit: B lacks both.
        self.assertDenied(self.orchestrator(self.merge_command(B)), "condition 4", STALE.format(n=2))
        self.assertNotIn(FILE_B, self.git(self.src, "ls-tree", "--name-only", "main"))
        # advance() does not park B at the merge: it sends the run back, commit approval kept.
        still = self.advance(B)
        self.assertEqual(("stale", ""), (still["action"], still["awaiting_human"]), still)
        self.assertEqual("test", still["stage"])
        self.assertEqual("test", self.run_row(B)["stage"])

    def sync_regate_and_merge_b(self):
        sync = f'cd "{self.tree(B).as_posix()}" && git merge main -m "Sync main"'
        self.assertAllowed(self.dev_agent("Bash", command=sync))
        self.git(self.tree(B), "merge", "main", "-m", "Sync main")
        self.assertTrue((self.tree(B) / FILE_A).is_file())

        # B walks back to the checkpoint through its own gates; the approved commit is kept,
        # so the run parks at done to wait for its merge.
        for stage, reached in (("test", "review"), ("review", "ready")):
            out = self.advance(B)
            self.assertEqual(("advanced", reached), (out["action"], out["stage"]), out)
            seen = self.assertRanInTree(B, stage)
            self.assertIn(FILE_A, seen["files"])  # the re-gate judged the synced tree
            self.assertIn(FILE_B, seen["files"])
        waiting = self.advance(B)
        self.assertEqual(("park", "merge"), (waiting["action"], waiting["awaiting_human"]), waiting)
        self.assertEqual("done", self.run_row(B)["stage"])

        self.assertAllowed(self.orchestrator(self.merge_command(B)))
        self.git(self.src, "merge", "--no-ff", "-m", f"Merge branch 'feature/{B}'", f"feature/{B}")
        done = self.advance(B)
        self.assertEqual("done", done["action"], done)
        self.assertIn("Worktree removed (the branch is kept)", done["message"])

    # --- 8. the end state ---------------------------------------------------------

    def nothing_is_left_behind(self):
        self.assertEqual([self.src.as_posix()], self.trees())
        self.assertFalse(self.tree(A).exists() or self.tree(B).exists())
        for task in (A, B):
            self.assertTrue(self.branch_exists(f"feature/{task}"), task)
            row = self.run_row(task)
            self.assertEqual(("done", ""), (row["stage"], row["awaiting_human"]), row)
            self.assertTrue((self.root / ".agentry" / "tasks" / "done" / f"{task}.md").is_file())
            self.assertFalse((self.root / ".agentry" / "tasks" / "active" / f"{task}.md").exists())
        for name, task in ((FILE_A, A), (FILE_B, B)):
            self.assertEqual(task, self.git(self.src, "show", f"main:{name}"))
            self.assertTrue((self.src / name).is_file())
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))
        code, out, err = self.spawn([self.script("worktree.py"), "--sweep"]).finish()
        self.assertEqual(0, code, err)
        self.assertEqual({"orphans": []}, json.loads(out))


class ConcurrentRegistrationTest(WorkspaceCase):

    def test_two_tasks_registered_at_the_same_moment_each_get_a_branch_and_a_tree(self):
        for task in (A, B):
            self.task_file(task)
        jobs = [self.advance_job(A), self.advance_job(B)]
        for task, job in zip((A, B), jobs, strict=True):
            out = self.answer(job)
            self.assertEqual("started", out["action"], out)
            self.assertEqual(f"src-wt/{task}", self.run_row(task)["worktree"])
            branch = self.git(self.tree(task), "rev-parse", "--abbrev-ref", "HEAD")
            self.assertEqual(f"feature/{task}", branch)
        self.assertEqual(3, len(self.trees()), self.trees())
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))


class StaleBaseAtTheCommitCheckpointTest(WorkspaceCase):

    def test_a_branch_the_trunk_moved_past_is_sent_back_to_test_before_the_checkpoint(self):
        self.task_file(C)
        started = self.advance(C, "--type", "bugfix")  # bugfix skips the plan step
        self.assertEqual(("started", "implement"), (started["action"], started["stage"]), started)
        self.assertEqual(f"bugfix/{C}", self.git(self.tree(C), "rev-parse", "--abbrev-ref", "HEAD"))
        (self.tree(C) / FILE_A).write_text("c\n", encoding="utf-8")
        self.assertEqual("test", self.advance(C)["stage"])
        self.assertEqual("review", self.advance(C)["stage"])

        # Someone else's work reaches the trunk while C is under review.
        (self.src / "elsewhere.txt").write_text("elsewhere\n", encoding="utf-8")
        self.git(self.src, "add", "elsewhere.txt")
        self.git(self.src, "commit", "-m", "elsewhere lands on main")

        stale = self.advance(C)
        self.assertEqual(("stale", "test"), (stale["action"], stale["stage"]), stale)
        self.assertIn(STALE.format(n=1), stale["message"])
        self.assertEqual("test", self.run_row(C)["stage"])

        self.git(self.tree(C), "merge", "main", "-m", "Sync main")
        self.assertEqual("review", self.advance(C)["stage"])
        self.assertEqual("ready", self.advance(C)["stage"])
        parked = self.advance(C)
        self.assertEqual(("park", "commit"), (parked["action"], parked["awaiting_human"]), parked)
        self.assertIn(FILE_A, parked["message"])


if __name__ == "__main__":
    unittest.main()

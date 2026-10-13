"""task-0114 (FR-50): what the PreToolUse gates and advance.py say to the HUMAN.

task-0024 gave the Stop hook a `systemMessage` for the person and left two groups
without one, because their output channel is not a Stop-hook document: the
PreToolUse denies (a stderr reason the model reads) and the `advance.py` refusals
(a Bash tool result the model reads).

Measured on claude.exe 2.1.295 with real `claude -p` runs and a throwaway hook:

- PreToolUse, exit code 2 + stderr + stdout `{"systemMessage": ...}`: the tool call
  is blocked on the stderr reason exactly as before, and the person gets a
  "PreToolUse:Bash says: <message>" notice. Exit code 2 and stderr are untouched,
  so no existing deny changes its contract.
- PostToolUse(Bash): the payload carries `tool_response.stdout` as a string, and a
  stdout `{"systemMessage": ...}` reaches the person as "PostToolUse:Bash says:".
- A subagent's stream showed the PreToolUse hook output but no notice; the notice
  is confirmed for the main thread only.

Supervisor decision, binding, overriding the task's NFR-4 criterion: if building the
human message raises, DROP THE MESSAGE AND KEEP THE DENY. A formatting error never
becomes an allow, least of all on a commit, a push or a plan. Every test below that
says "keeps the deny" pins that for one gate.

N of N: the table in DenySiteAccountingTest is every `deny(` call in pretool_gate.py
(52) and agent_gate.py (27), classified. Five pretool_gate sites ask the CEO for
something (a plan, a commit, a push, a merge's commit approval, a step left to the
human by an unattended session); the rest are policy denies and say nothing to him.
agent_gate asks nobody: its denies are role boundaries.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import ast
import collections
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
PIPELINE_DIR = TOOLS_DIR / "pipeline"
HOOKS_DIR = TOOLS_DIR / "hooks"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import agent_gate
import pretool_gate
import refusal_notice
import state
import tmproot
from test_conveyor_gaps import BUILD_PIPELINE
from test_pretool_gate_git import denial_reason
from test_trunk_push_by_ref import BRANCH, TASK, task_branch_checked_out, trunk_checked_out

# The model's vocabulary. If one of these shows up in a sentence meant for the CEO
# the two audiences have been merged again (same list as test_human_output.py).
MODEL_ONLY = ("advance.py", "approve.py", "AskUserQuestion", "Do not ask", "--reject")
DASHES = tuple(chr(code) for code in (0x2014, 0x2013))

# Every `deny(` call, by the function that holds it. A new deny anywhere changes a
# number here and fails DenySiteAccountingTest until it is classified.
PRETOOL_DENIES = {
    "binding_deny": 1, "check_bare_nul": 1, "check_branch_base": 1, "check_branch_creation": 2,
    "check_commit_attribution": 1, "check_destructive_and_repl": 4,
    "check_merge_source": 5, "check_nul_redirect": 1, "check_piped_test_suite": 1,
    "check_push_target": 4, "check_trunk_merge": 2, "check_unattended": 1,
    "evaluate": 1, "finish": 1,
    "handle_bash": 3, "handle_edit": 5, "judge_git": 9, "orch_check_bash": 4,
    "orch_check_edit": 1, "placement_deny": 1, "plan_deny": 1, "trunk_freeze_deny": 1,
    "unc_deny": 1,
}
# The subset that asks the human: it passes `human=`. judge_git (the git half of
# handle_bash, run once per commit / merge / pull / push while worktrees are in play)
# holds the commit and the push; plan_deny is the one plan site, called from handle_bash
# and handle_edit.
PRETOOL_ASKING = {"check_merge_source": 1, "check_unattended": 1, "judge_git": 2,
                  "plan_deny": 1}
PLAN_DENY_CALLERS = {"handle_bash": 1, "handle_edit": 1}
AGENT_DENIES = {"bash_prelude": 6, "check_program": 1, "evaluate": 2, "handle_dev": 7,
                "handle_docs": 3, "handle_readonly": 1, "handle_web": 1,
                "lexical_bash_checks": 1,
                "memory_target_deny": 1, "git_target_deny": 1, "restricted_bash": 2,
                "worktree_confinement": 1}


def deny_sites(path: Path) -> tuple:
    """(deny calls by function, those passing human=, plan_deny calls by function)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    every, asking, via_plan = collections.Counter(), collections.Counter(), collections.Counter()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in ("deny", "plan_deny"):
            continue
        holder = node
        while not isinstance(holder, ast.FunctionDef):
            holder = parent[holder]
        if node.func.id == "plan_deny":
            via_plan[holder.name] += 1
            continue
        every[holder.name] += 1
        if any(kw.arg == "human" for kw in node.keywords):
            asking[holder.name] += 1
    return dict(every), dict(asking), dict(via_plan)


def run_main(module, payload: dict, argv: list | None = None) -> tuple:
    """(exit code, stdout, stderr) of module.main() fed one hook payload."""
    out, err = io.StringIO(), io.StringIO()
    with unittest.mock.patch.object(pretool_gate, "read_payload", return_value=payload), \
            unittest.mock.patch.object(sys, "argv", argv or ["gate"]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main()
    return code, out.getvalue(), err.getvalue()


def bash(command: str, cwd: str = ".", **extra) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, **extra}


# Each driver reaches ONE asking deny through the real gate, end to end, and returns
# (exit code, stdout, stderr) of the entry point it was handed. `entry` is
# (module, argv, extra payload keys): the main thread, or a dev agent.
def drive_commit(entry):
    module, argv, extra = entry
    with task_branch_checked_out({TASK: {}}) as repo:
        return run_main(module, bash('git commit -m "x"', repo.path, **extra), argv)


def drive_push(entry):
    module, argv, extra = entry
    with task_branch_checked_out({TASK: {}}) as repo:
        return run_main(module, bash(f"git push origin {BRANCH}", repo.path, **extra), argv)


def drive_merge(entry):
    module, argv, extra = entry
    with trunk_checked_out({TASK: {}}) as repo:
        return run_main(module, bash(f"git merge {BRANCH}", repo.path, **extra), argv)


def drive_plan(entry):
    module, argv, extra = entry
    payload = {"tool_name": "Write", "agent_type": "dev", **extra,
               "tool_input": {"file_path": "src/app/main.py", "content": "x"}}
    with unittest.mock.patch.object(pretool_gate, "plan_write_allowed", return_value=False), \
            unittest.mock.patch.object(pretool_gate, "plan_blocked_task",
                                       return_value=TASK):
        return run_main(module, payload, argv)


def drive_unattended(entry):
    module, argv, extra = entry
    with unittest.mock.patch.dict(os.environ, {pretool_gate.UNATTENDED_ENV: "1"}):
        return run_main(module, bash("git push origin feature/task-0001", ".", **extra), argv)


# kind -> (driver, a phrase the sentence must carry, a phrase the stderr reason carries)
ASKING = {
    "commit": (drive_commit, f"{TASK} is ready to commit", f"Commit for {TASK} is not approved"),
    "push": (drive_push, f"{TASK} is ready to push", f"Push for {TASK} is not approved"),
    "merge": (drive_merge, f"{TASK} cannot be merged into main", "condition 3 of 3 failed"),
    "plan": (drive_plan, f"{TASK} tried to change code", "has not approved the plan"),
    "unattended": (drive_unattended, "stopped at a push", "started UNATTENDED"),
}
# Templates that raise KeyError when formatted: a message that cannot be built.
BROKEN = {kind: "{nothing_fills_this}" for kind in pretool_gate.HUMAN_NOTICES}
MAIN_THREAD = (pretool_gate, None, {})
DEV_AGENT = (agent_gate, ["agent_gate", "--profile", "dev"], {})


class DenySiteAccountingTest(unittest.TestCase):
    """N of N: every deny in both gates, classified as asking the human or not."""

    def test_pretool_gate_has_exactly_the_classified_denies(self):
        every, asking, via_plan = deny_sites(PIPELINE_DIR / "pretool_gate.py")
        self.assertEqual(PRETOOL_DENIES, every,
                         "a deny() was added or removed in pretool_gate.py: decide whether "
                         "it asks the CEO for something (then give it human=) and update "
                         "this table")
        self.assertEqual(52, sum(every.values()))
        self.assertEqual(PRETOOL_ASKING, asking)
        self.assertEqual(PLAN_DENY_CALLERS, via_plan)

    def test_agent_gate_asks_nobody(self):
        every, asking, via_plan = deny_sites(PIPELINE_DIR / "agent_gate.py")
        self.assertEqual(AGENT_DENIES, every,
                         "a deny() was added or removed in agent_gate.py: a role boundary "
                         "asks the CEO for nothing; if this one does, it needs a human= "
                         "channel that agent_gate does not have yet")
        self.assertEqual(27, sum(every.values()))
        self.assertEqual({}, asking)
        self.assertEqual({}, via_plan)

    def test_agent_deny_takes_no_human_argument(self):
        # The signature is the proof there is no second channel hiding in agent_gate.
        self.assertEqual(["reason"], list(agent_gate.deny.__code__.co_varnames[:1]))
        self.assertEqual(1, agent_gate.deny.__code__.co_argcount)

    def test_every_template_has_exactly_one_asking_caller_and_one_driver(self):
        source = (PIPELINE_DIR / "pretool_gate.py").read_text(encoding="utf-8")
        for kind in pretool_gate.HUMAN_NOTICES:
            with self.subTest(kind=kind):
                self.assertEqual(1, source.count(f'human_notice("{kind}"'))
        self.assertEqual(set(pretool_gate.HUMAN_NOTICES), set(ASKING))

    def test_each_sentence_says_what_is_owed_and_is_written_for_a_person(self):
        samples = {"task": TASK, "trunk": "main", "step": "a push"}
        for kind in pretool_gate.HUMAN_NOTICES:
            with self.subTest(kind=kind):
                sentence = pretool_gate.human_notice(kind, **samples)
                self.assertTrue(sentence.strip())
                self.assertTrue(
                    any(word in sentence.lower() for word in ("approve", "take that step")),
                    sentence)
                for word in MODEL_ONLY:
                    self.assertNotIn(word, sentence)
                for dash in DASHES:
                    self.assertNotIn(dash, sentence)


class AskingDenyReachesTheHumanTest(unittest.TestCase):
    """Each asking deny prints one systemMessage and still denies on stderr, from the
    main thread and from a dev agent, which reaches pretool_gate through agent_gate."""

    def check(self, kind: str, entry):
        driver, human_phrase, reason_phrase = ASKING[kind]
        code, out, err = driver(entry)
        self.assertEqual(2, code, err)
        self.assertIn(reason_phrase, err)
        doc = json.loads(out)
        self.assertEqual(["systemMessage"], list(doc))
        self.assertIn(human_phrase, doc["systemMessage"])
        self.assertNotIn(doc["systemMessage"], err)
        self.assertEqual(1, out.count("\n"))

    def test_the_main_thread_hears_every_asking_deny(self):
        for kind in ASKING:
            with self.subTest(kind=kind):
                self.check(kind, MAIN_THREAD)

    def test_a_dev_agent_hears_the_asking_denies_that_reach_pretool_gate(self):
        for kind in ("commit", "push", "plan"):
            with self.subTest(kind=kind):
                self.check(kind, DEV_AGENT)


class PolicyDenyStaysSilentTest(unittest.TestCase):
    """A deny that asks the CEO for nothing prints nothing on stdout."""

    TRAILER = 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'

    def test_a_policy_deny_in_pretool_gate_has_no_system_message(self):
        code, out, err = run_main(pretool_gate, bash(self.TRAILER))
        self.assertEqual(2, code)
        self.assertIn("AI-authorship trailer", err)
        self.assertEqual("", out)

    def test_a_role_boundary_in_agent_gate_has_no_system_message(self):
        for profile, command in (("dev", "python approve.py --task task-1 --gate commit"),
                                 ("readonly", "rm -rf build"),
                                 ("docs", "rm -rf build")):
            with self.subTest(profile=profile):
                code, out, err = run_main(agent_gate, bash(command),
                                          ["agent_gate", "--profile", profile])
                self.assertEqual(2, code)
                self.assertTrue(err.strip())
                self.assertEqual("", out)

    def test_an_allowed_call_prints_nothing(self):
        code, out, err = run_main(pretool_gate, bash("ls"))
        self.assertEqual((0, "", ""), (code, out, err))

    def test_the_first_deny_decides_who_is_told(self):
        # handle_bash computes its checks eagerly: a later asking deny must not leak
        # its sentence onto the policy deny that is actually returned.
        del pretool_gate.PENDING_HUMAN[:]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pretool_gate.deny("policy")
            pretool_gate.deny("asking", human="you owe a commit approval")
        self.assertEqual([""], pretool_gate.PENDING_HUMAN)
        del pretool_gate.PENDING_HUMAN[:]
        with contextlib.redirect_stderr(err):
            pretool_gate.deny("asking", human="first")
            pretool_gate.deny("asking", human="second")
        self.assertEqual(["first"], pretool_gate.PENDING_HUMAN)
        del pretool_gate.PENDING_HUMAN[:]

    def test_a_stale_sentence_from_an_earlier_call_is_not_printed(self):
        pretool_gate.PENDING_HUMAN[:] = ["left over from a direct handle_bash call"]
        code, out, _err = run_main(pretool_gate, bash(self.TRAILER))
        self.assertEqual((2, ""), (code, out))


class HumanMessageFailureKeepsTheDenyTest(unittest.TestCase):
    """The supervisor's amendment, per gate: a message that cannot be built, or
    printed, is dropped and the deny stands. Never an allow."""

    def test_pretool_gate_keeps_every_asking_deny_when_the_template_raises(self):
        for kind, (driver, _human, reason) in ASKING.items():
            with self.subTest(kind=kind), \
                    unittest.mock.patch.dict(pretool_gate.HUMAN_NOTICES, BROKEN):
                code, out, err = driver(MAIN_THREAD)
                self.assertEqual(2, code, f"{kind}: a template error must not allow")
                self.assertIn(reason, err)
                self.assertEqual("", out)

    def test_pretool_gate_keeps_the_commit_push_and_plan_denies_when_building_raises(self):
        # The three the supervisor named, with the lookup itself raising.
        class Exploding(dict):
            def __getitem__(self, key):
                raise RuntimeError("boom")

        for kind in ("commit", "push", "plan"):
            with self.subTest(kind=kind), \
                    unittest.mock.patch.object(pretool_gate, "HUMAN_NOTICES", Exploding()):
                code, out, err = ASKING[kind][0](MAIN_THREAD)
                self.assertEqual(2, code)
                self.assertIn(ASKING[kind][2], err)
                self.assertEqual("", out)

    def test_pretool_gate_keeps_the_deny_when_the_message_cannot_be_printed(self):
        for kind in ("commit", "push", "plan"):
            with self.subTest(kind=kind), unittest.mock.patch.object(
                    pretool_gate.json, "dumps", side_effect=RuntimeError("boom")):
                code, out, err = ASKING[kind][0](MAIN_THREAD)
                self.assertEqual(2, code)
                self.assertIn(ASKING[kind][2], err)
                self.assertEqual("", out)

    def test_a_human_value_that_cannot_become_text_does_not_change_the_verdict(self):
        class Unprintable:
            def __bool__(self):
                raise RuntimeError("boom")

        del pretool_gate.PENDING_HUMAN[:]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.deny("reason", human=Unprintable())
        self.assertEqual(2, code)
        self.assertEqual("reason\n", err.getvalue())
        del pretool_gate.PENDING_HUMAN[:]

    def test_human_notice_never_raises(self):
        self.assertEqual("", pretool_gate.human_notice("no-such-kind"))
        self.assertEqual("", pretool_gate.human_notice("commit"))  # missing {task}
        with unittest.mock.patch.dict(pretool_gate.HUMAN_NOTICES, BROKEN):
            self.assertEqual("", pretool_gate.human_notice("commit", task=TASK))

    def test_agent_gate_keeps_the_deny_a_dev_agent_earns_when_the_template_raises(self):
        for kind in ("commit", "push", "plan"):
            with self.subTest(kind=kind), \
                    unittest.mock.patch.dict(pretool_gate.HUMAN_NOTICES, BROKEN):
                code, out, err = ASKING[kind][0](DEV_AGENT)
                self.assertEqual(2, code)
                self.assertIn(ASKING[kind][2], err)
                self.assertEqual("", out)

    def test_agent_gate_keeps_the_deny_when_stdout_is_broken(self):
        class Broken(io.StringIO):
            def write(self, text):
                raise OSError("stdout is gone")

        for kind in ("commit", "plan"):
            with self.subTest(kind=kind), \
                    unittest.mock.patch.object(pretool_gate.sys, "stdout", Broken()):
                code, _out, err = ASKING[kind][0](DEV_AGENT)
                self.assertEqual(2, code)
                self.assertIn(ASKING[kind][2], err)

    def test_agent_gate_policy_denies_are_untouched_by_the_channel(self):
        with unittest.mock.patch.dict(pretool_gate.HUMAN_NOTICES, BROKEN):
            code, out, err = run_main(agent_gate, bash("python approve.py --task t --gate x"),
                                      ["agent_gate", "--profile", "dev"])
        self.assertEqual((2, ""), (code, out))
        self.assertIn("orchestrator-only", err)


class RealProcessTest(unittest.TestCase):
    """The shipped script, as Claude Code starts it: exit code, stderr and stdout."""

    def test_pretool_gate_script_denies_on_stderr_and_speaks_on_stdout(self):
        payload = json.dumps(bash("git push origin feature/task-0001", str(TOOLS_DIR)))
        env = {**os.environ, pretool_gate.UNATTENDED_ENV: "1"}
        done = subprocess.run([sys.executable, str(PIPELINE_DIR / "pretool_gate.py")],
                              input=payload.encode("utf-8"), capture_output=True,
                              env=env, timeout=120)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn(b"started UNATTENDED", done.stderr)
        doc = json.loads(done.stdout.decode("utf-8"))
        self.assertEqual(["systemMessage"], list(doc))
        self.assertIn("unattended session", doc["systemMessage"])

    def test_agent_gate_script_is_silent_on_stdout_for_a_role_boundary(self):
        payload = json.dumps(bash("python approve.py --task task-1 --gate commit", "."))
        done = subprocess.run(
            [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), "--profile", "readonly"],
            input=payload.encode("utf-8"), capture_output=True, timeout=120)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual(b"", done.stdout)


# --- advance.py refusals -------------------------------------------------------

SPEC = "spec-9000"
NEW_TASK = "task-9000"
CRITERIA = "## Acceptance Criteria\n\n- [ ] the refusal reaches the CEO\n"


class RefusalHarness(unittest.TestCase):
    """advance.main() against a sandbox: a task file, a spec file, an empty run store."""

    spec_status = "draft"
    task_spec = SPEC

    def setUp(self):
        sandbox = tmproot.sandbox(self, "advance_human_")
        self.root = sandbox
        specs = sandbox / ".agentry" / "specs"
        specs.mkdir(parents=True)
        (specs / f"{SPEC}-x.md").write_text(
            f"---\nid: {SPEC}\nstatus: {self.spec_status}\n---\nbody\n", encoding="utf-8")
        self.dirs = {name: sandbox / name for name in ("backlog", "active", "done")}
        for directory in self.dirs.values():
            directory.mkdir()
        self.task_file = self.dirs["backlog"] / f"{NEW_TASK}.md"
        self.task_file.write_text(
            f"---\nid: {NEW_TASK}\nspec: {self.task_spec}\n---\n\n{CRITERIA}",
            encoding="utf-8")
        for target, attr, value in (
                (state, "ROOT", sandbox),
                (state, "STATE_DIR", sandbox),
                (state, "DB_PATH", sandbox / "run.db"),
                (state, "TASK_DIRS", self.dirs)):
            patcher = unittest.mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("handoff_debt", "memory_debt"):
            patcher = unittest.mock.patch.object(advance, name, return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def advance(self) -> tuple:
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", NEW_TASK]), \
                contextlib.redirect_stdout(out):
            code = advance.main()
        return code, out.getvalue()

    def registered(self) -> bool:
        conn = state.connect()
        try:
            return state.get_run(conn, NEW_TASK) is not None
        finally:
            conn.close()


class UnapprovedSpecRefusalReachesTheHumanTest(RefusalHarness):

    def test_the_refusal_carries_a_sentence_for_the_ceo(self):
        code, out = self.advance()
        doc = json.loads(out)
        self.assertEqual(0, code)
        self.assertEqual("refused", doc["action"])
        self.assertIn("only an approved spec may be implemented", doc["message"])
        self.assertIn("Surface it to the CEO", doc["message"])
        self.assertIn(NEW_TASK, doc["human"])
        self.assertIn("spec is not approved", doc["human"])
        for word in MODEL_ONLY:
            self.assertNotIn(word, doc["human"])
        for dash in DASHES:
            self.assertNotIn(dash, doc["human"])

    def test_the_refused_task_is_not_registered_or_moved(self):
        self.advance()
        self.assertFalse(self.registered())
        self.assertTrue(self.task_file.is_file())

    def test_the_model_message_is_byte_identical_to_before(self):
        _code, out = self.advance()
        self.assertEqual(
            f"{NEW_TASK}: spec '{SPEC}' has status 'draft' - only an approved spec may be "
            f"implemented. Surface it to the CEO.", json.loads(out)["message"])

    def test_a_template_that_cannot_be_built_drops_the_sentence_and_keeps_the_refusal(self):
        with unittest.mock.patch.object(advance, "SPEC_NOT_APPROVED_HUMAN", "{nothing_fills}"):
            _code, out = self.advance()
        doc = json.loads(out)
        self.assertEqual("refused", doc["action"])
        self.assertNotIn("human", doc)
        self.assertFalse(self.registered())
        self.assertTrue(self.task_file.is_file())

    def test_the_hook_turns_the_real_refusal_into_a_system_message(self):
        _code, out = self.advance()
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": f"python advance.py --task {NEW_TASK}"},
                   "tool_response": {"stdout": out, "stderr": ""}}
        done = subprocess.run([sys.executable, str(HOOKS_DIR / "refusal_notice.py")],
                              input=json.dumps(payload).encode("utf-8"),
                              capture_output=True, timeout=120)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual({"systemMessage": json.loads(out)["human"]},
                         json.loads(done.stdout.decode("utf-8")))


class ApprovedSpecIsNotRefusedTest(RefusalHarness):
    spec_status = "approved"

    def test_a_task_with_an_approved_spec_registers_and_says_nothing_to_the_human(self):
        _code, out = self.advance()
        doc = json.loads(out)
        self.assertEqual("started", doc["action"])
        self.assertNotIn("human", doc)
        self.assertTrue(self.registered())


class RefusalsThatAskNobodyTest(RefusalHarness):
    """Every other refusal is work for an agent: no `human` key."""

    def refusal(self) -> dict:
        return json.loads(self.advance()[1])

    def test_no_spec_field(self):
        self.task_file.write_text(f"---\nid: {NEW_TASK}\n---\n\n{CRITERIA}", encoding="utf-8")
        doc = self.refusal()
        self.assertEqual("refused", doc["action"])
        self.assertNotIn("human", doc)

    def test_spec_file_missing(self):
        self.task_file.write_text(
            f"---\nid: {NEW_TASK}\nspec: spec-0000\n---\n\n{CRITERIA}", encoding="utf-8")
        doc = self.refusal()
        self.assertIn("no such file", doc["message"])
        self.assertNotIn("human", doc)

    def test_empty_acceptance_criteria(self):
        self.task_file.write_text(
            f"---\nid: {NEW_TASK}\nspec: {SPEC}\n---\n\n## Acceptance Criteria\n\n",
            encoding="utf-8")
        doc = self.refusal()
        self.assertIn("Acceptance Criteria", doc["message"])
        self.assertNotIn("human", doc)

    def test_handoff_debt(self):
        debt = [{"task": "task-8000", "reason": "no handoff doc"}]
        with unittest.mock.patch.object(advance, "handoff_debt", return_value=debt):
            doc = self.refusal()
        self.assertIn("handoff debt", doc["message"])
        self.assertNotIn("human", doc)

    def test_memory_debt(self):
        with unittest.mock.patch.object(advance, "memory_debt",
                                        return_value=[{"task": "task-8000", "reason": "x"}]):
            doc = self.refusal()
        self.assertIn("memory debt", doc["message"])
        self.assertNotIn("human", doc)

    def test_refusal_human_is_empty_for_all_of_them(self):
        for reason in ("task-1: no 'spec:' field in frontmatter.",
                       "task-1: handoff debt blocks registration",
                       "task-1: memory debt blocks registration",
                       "task-1: the file sits in tasks/done", ""):
            with self.subTest(reason=reason):
                self.assertEqual("", advance.refusal_human("task-1", reason))


class RefusalNoticeHookTest(unittest.TestCase):

    REFUSED = json.dumps({"action": "refused", "task": "task-1", "message": "m",
                          "human": advance.SPEC_NOT_APPROVED_HUMAN.format(task="task-1")},
                         indent=2)

    def payload(self, stdout, command="python advance.py --task task-1", tool="Bash"):
        return {"tool_name": tool, "tool_input": {"command": command},
                "tool_response": {"stdout": stdout, "stderr": ""}}

    def run_hook(self, raw: bytes) -> tuple:
        out = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        with unittest.mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            code = refusal_notice.main()
        return code, out.getvalue()

    def says(self, payload: dict) -> str:
        return refusal_notice.human_line(payload)

    def test_a_refusal_with_a_sentence_becomes_a_system_message(self):
        code, out = self.run_hook(json.dumps(self.payload(self.REFUSED)).encode("utf-8"))
        self.assertEqual(0, code)
        self.assertEqual({"systemMessage": advance.SPEC_NOT_APPROVED_HUMAN.format(task="task-1")},
                         json.loads(out))

    def test_a_refusal_without_a_sentence_says_nothing(self):
        plain = json.dumps({"action": "refused", "task": "task-1", "message": "m"})
        self.assertEqual("", self.says(self.payload(plain)))

    def test_other_actions_say_nothing(self):
        for action in ("started", "advanced", "park", "blocked", "gate_failed"):
            doc = json.dumps({"action": action, "human": "never shown"})
            with self.subTest(action=action):
                self.assertEqual("", self.says(self.payload(doc)))

    def test_other_commands_and_tools_say_nothing(self):
        self.assertEqual("", self.says(self.payload(self.REFUSED, command="cat notes.txt")))
        self.assertEqual("", self.says(self.payload(self.REFUSED, tool="Edit")))

    def test_output_in_front_of_the_json_says_nothing_and_trailing_text_is_tolerated(self):
        self.assertEqual("", self.says(self.payload("warning: something\n" + self.REFUSED)))
        self.assertIn("spec is not approved",
                      self.says(self.payload(self.REFUSED + "\ntrailing text\n")))

    def test_a_response_that_is_a_plain_string_is_read(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "advance.py --task task-1"},
                   "tool_response": self.REFUSED}
        self.assertIn("spec is not approved", self.says(payload))

    def test_garbage_in_prints_nothing_and_exits_zero(self):
        for raw in (b"", b"not json", b"[]", b"null", b"\xff\xfe",
                    json.dumps({"tool_name": "Bash"}).encode("utf-8"),
                    json.dumps(self.payload("{ not json")).encode("utf-8"),
                    json.dumps(self.payload(None)).encode("utf-8"),
                    json.dumps(self.payload(json.dumps(
                        {"action": "refused", "human": 7}))).encode("utf-8"),
                    json.dumps(self.payload(json.dumps([1, 2]))).encode("utf-8")):
            with self.subTest(raw=raw[:30]):
                self.assertEqual((0, ""), self.run_hook(raw))

    def test_a_failure_while_building_the_message_exits_zero_quietly(self):
        with unittest.mock.patch.object(refusal_notice, "human_line",
                                        side_effect=RuntimeError("boom")):
            self.assertEqual((0, ""), self.run_hook(
                json.dumps(self.payload(self.REFUSED)).encode("utf-8")))


class SettingsWiringTest(unittest.TestCase):

    SETTINGS = TOOLS_DIR.parent / "settings.json"

    def bash_post_hooks(self) -> list:
        settings = json.loads(self.SETTINGS.read_text(encoding="utf-8"))
        entries = [e for e in settings["hooks"]["PostToolUse"] if e.get("matcher") == "Bash"]
        return [h["command"] for e in entries for h in e["hooks"]]

    def test_the_notice_hook_is_wired_on_post_tool_use_bash(self):
        commands = self.bash_post_hooks()
        wired = [c for c in commands if "hooks/refusal_notice.py" in c]
        self.assertEqual(1, len(wired), commands)
        self.assertTrue((HOOKS_DIR / "refusal_notice.py").is_file())

    def test_the_nul_cleanup_hook_is_still_there(self):
        self.assertIn("rm -f nul NUL", self.bash_post_hooks())


class DenyContractUnchangedTest(unittest.TestCase):
    """Existing callers still see exit code 2 and the reason on stderr only."""

    def test_handle_bash_still_returns_two_with_the_reason_on_stderr(self):
        with task_branch_checked_out({TASK: {}}) as repo:
            code, err = denial_reason('git commit -m "x"', repo.path)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK} is not approved yet", err)
        self.assertNotIn("systemMessage", err)

    def test_the_approved_commit_still_passes(self):
        with task_branch_checked_out({TASK: {"commit_approved": 1}}) as repo:
            code, err = denial_reason('git commit -m "x"', repo.path)
        self.assertEqual((0, ""), (code, err))


if __name__ == "__main__":
    unittest.main()

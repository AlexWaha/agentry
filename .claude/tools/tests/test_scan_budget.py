"""The scan budget (task-0112): a command the gate cannot afford to scan is refused.

`redirect_write_fragments('"$(' * 100)` took 27 s on the code before this task (82 s on
a clean checkout), so a 300-character command could outlast the hook timeout, and a
hook that times out is an allow. The cost was never one primitive; it was the fan-out
of the recursing gates over a nested-body list that is flattened again at every level.

Each case below pins one half of the contract: the pathological input finishes in under
a second and is DENIED (fail closed, with a message that says why), while ordinary input
- including the large and the heavily substituted - is not touched.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
from test_pretool_gate_git import TempRepo, denial_reason, gate_state
from test_script_gate_evasions import PROFILES, run_profile

LIMIT_SECONDS = 1.0
TASK = "task-9112"
WORK_BRANCH = "bugfix/task-9112"

PATHOLOGICAL = {
    "100 dq-substitution openers": '"$(' * 100,
    "400 dq-substitution openers": '"$(' * 400,
    "eval chain": "eval $(" * 60,
    "closed substitution stack": "echo $(echo " * 40 + ")" * 40,
    "6000-character opener run": '"$(' * 2000,
    "unterminated heredoc openers": "cat <<EOF\n" * 3000,
}


def timed(fn):
    start = time.monotonic()
    result = fn()
    return result, time.monotonic() - start


class PathologicalInputIsRefusedFastTest(unittest.TestCase):

    def setUp(self):
        pretool_gate._too_complex.cache_clear()

    def test_the_named_case_is_refused_in_under_a_second(self):
        command = '"$(' * 100
        self.assertEqual(300, len(command))
        fragments, took = timed(lambda: list(pretool_gate.redirect_write_fragments(command)))
        self.assertLess(took, LIMIT_SECONDS)
        self.assertEqual([pretool_gate.TOO_COMPLEX], fragments)

    def test_every_shape_is_too_complex_and_answered_in_under_a_second(self):
        for name, command in PATHOLOGICAL.items():
            pretool_gate._too_complex.cache_clear()
            with self.subTest(shape=name):
                verdict, took = timed(lambda: pretool_gate.too_complex(command))
                self.assertTrue(verdict)
                self.assertLess(took, LIMIT_SECONDS)

    def test_the_main_thread_gate_denies_with_its_own_message(self):
        for name, command in PATHOLOGICAL.items():
            pretool_gate._too_complex.cache_clear()
            with self.subTest(shape=name):
                (code, err), took = timed(lambda: denial_reason(command, str(TESTS_DIR)))
                self.assertEqual(2, code, err)
                self.assertIn("too complex to check", err)
                self.assertLess(took, LIMIT_SECONDS)

    def test_every_agent_profile_denies_and_never_allows(self):
        for name, command in PATHOLOGICAL.items():
            for profile in PROFILES:
                pretool_gate._too_complex.cache_clear()
                with self.subTest(shape=name, profile=profile):
                    (code, err), took = timed(lambda: run_profile(profile, command))
                    self.assertEqual(2, code, err)
                    self.assertLess(took, LIMIT_SECONDS)

    def test_the_recursing_gates_refuse_when_asked_directly(self):
        command = '"$(' * 100
        for fn in (pretool_gate.runs_approve_script, pretool_gate.redirects_to_bare_nul,
                   pretool_gate.redirects_to_dev_null):
            pretool_gate._too_complex.cache_clear()
            with self.subTest(gate=fn.__name__):
                verdict, took = timed(lambda: fn(command))
                self.assertTrue(verdict)
                self.assertLess(took, LIMIT_SECONDS)
        for fn in (lambda c: pretool_gate.git_invokes(c, "push"),
                   pretool_gate.eval_redirect, pretool_gate.plan_mutating_command):
            pretool_gate._too_complex.cache_clear()
            verdict, took = timed(lambda: fn(command))
            self.assertTrue(verdict)
            self.assertLess(took, LIMIT_SECONDS)

    def test_the_depth_check_asks_at_the_root_visit(self):
        self.assertTrue(pretool_gate.unscannable_depth('"$(' * 100, 0))
        self.assertFalse(pretool_gate.unscannable_depth("git status", 0))
        self.assertFalse(pretool_gate.unscannable_depth("echo $(date)", 0))

    def test_no_shape_that_passes_the_budget_can_stall_a_gate(self):
        # The budget is what bounds the gates, so a command just under it must still
        # be quick: these sizes straddle where each shape starts to be refused.
        shapes = (lambda n: "echo $(echo " * n + ")" * n, lambda n: "eval $(" * n,
                  lambda n: '"$(' * n + ")" * n)
        for make in shapes:
            for n in (16, 24, 32, 64):
                command = make(n)
                for profile in ("handle_dev", "handle_readonly"):
                    pretool_gate._too_complex.cache_clear()
                    with self.subTest(n=n, profile=profile, head=command[:20]):
                        _, took = timed(lambda: run_profile(profile, command))
                        self.assertLess(took, LIMIT_SECONDS)

    def test_the_tokeniser_answers_unparseable_so_every_caller_refuses(self):
        self.assertIsNone(pretool_gate.gate_tokens('"$(' * 100))
        self.assertIsNone(pretool_gate.command_segments('"$(' * 100))
        self.assertTrue(agent_gate.unparseable('"$(' * 100))

    def test_an_exhausted_budget_does_not_leak_into_the_next_command(self):
        self.assertTrue(pretool_gate.too_complex('"$(' * 100))
        self.assertIsNone(pretool_gate._SCAN)
        self.assertFalse(pretool_gate.too_complex("git status"))
        self.assertEqual("x", pretool_gate.mask_quoted("x"))
        self.assertEqual(["git", "status"], pretool_gate.gate_tokens("git status"))

    def test_the_verdict_is_cached_per_command(self):
        command = '"$(' * 100
        self.assertTrue(pretool_gate.too_complex(command))
        _, again = timed(lambda: pretool_gate.too_complex(command))
        self.assertLess(again, 0.01)


class OrdinaryInputIsUntouchedTest(unittest.TestCase):

    def setUp(self):
        pretool_gate._too_complex.cache_clear()

    ORDINARY = (
        "ls -la",
        "git status && git log --oneline -5",
        'git commit -m "fix (x): handle $(none) case"',
        "echo $(git rev-parse HEAD) $(date +%F)",
        "echo $(echo $(echo $(echo nested)))",
        'bash -c "bash -c \'echo hi\'"',
        "cat <<'EOF' > notes.txt\nline with \"quotes\" and $(substitution) text\nEOF\n",
        "python -c \"print('a > b')\" | cat",
        "x=$(grep -c \"it(\" file.txt); echo \"$x\"",
        "for f in $(ls); do echo \"$f\"; done",
    )

    def test_ordinary_commands_are_not_too_complex(self):
        for command in self.ORDINARY:
            with self.subTest(command=command):
                self.assertFalse(pretool_gate.too_complex(command))
                self.assertIsNotNone(pretool_gate.gate_tokens(command))

    def test_two_hundred_substitutions_stay_scannable(self):
        command = "echo " + " ".join(f"$(git rev-parse HEAD~{i})" for i in range(200))
        verdict, took = timed(lambda: pretool_gate.too_complex(command))
        self.assertFalse(verdict)
        self.assertLess(took, LIMIT_SECONDS)

    def test_a_large_heredoc_write_stays_scannable(self):
        body = 'name = "value"\nprint(f"{a} > {b}")  # it\'s $(not a substitution)\n' * 600
        for opener in ("<<'EOF'", "<<EOF"):
            command = f"cat > out.txt {opener}\n{body}EOF\n"
            self.assertGreater(len(command), 30_000)
            with self.subTest(opener=opener):
                self.assertFalse(pretool_gate.too_complex(command))

    def test_the_budget_is_off_outside_the_walk(self):
        # Thousands of direct calls (the unit tests do exactly this) never accumulate.
        for _ in range(2000):
            pretool_gate.mask_quoted('echo "a > b" \'c\' "$(echo "d")"')
            pretool_gate.command_substitutions("echo $(a) $(b $(c))")
        self.assertIsNone(pretool_gate._SCAN)

    def test_asking_from_inside_the_walk_does_not_restart_the_budget(self):
        pretool_gate._SCAN = [10]
        try:
            self.assertFalse(pretool_gate.too_complex('"$(' * 100))
            self.assertEqual([10], pretool_gate._SCAN)
        finally:
            pretool_gate._SCAN = None

    def test_the_charge_stops_a_walk_that_overspends(self):
        pretool_gate._SCAN = [5]
        try:
            with self.assertRaises(pretool_gate.ScanBudgetExceeded):
                pretool_gate._charge(6)
        finally:
            pretool_gate._SCAN = None
        pretool_gate._charge(10**9)  # no walk, no charge

    def test_a_dev_agent_still_runs_an_ordinary_command(self):
        for command in ("ls -la", "git status", 'echo "$(pwd)"'):
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(0, code, err)


class SubstitutionOverflowIsNotDroppedTest(unittest.TestCase):
    """substitution_bodies() cut its list at 64 and dropped the rest, so a push behind
    64 harmless substitutions was never scanned (measured: git_invokes said False)."""

    FILLER = "$(:) " * 70

    def setUp(self):
        pretool_gate._too_complex.cache_clear()

    def test_a_push_behind_sixty_four_substitutions_is_still_seen(self):
        command = f"echo {self.FILLER}$(git push origin main)"
        self.assertTrue(pretool_gate.git_invokes(command, "push"))

    def test_the_main_thread_gate_denies_that_push(self):
        command = f"echo {self.FILLER}$(git push origin main)"
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            code, err = denial_reason(command, repo.path)
        self.assertEqual(2, code, err)

    def test_a_redirect_behind_sixty_four_substitutions_is_still_seen(self):
        command = f"echo {self.FILLER}$(echo x > out.txt)"
        self.assertTrue(any("out.txt" in frag
                            for frag in pretool_gate.redirect_write_fragments(command)))

    def test_the_overflow_arrives_as_one_more_body_a_line_each(self):
        bodies = pretool_gate.substitution_bodies("echo " + "$(echo a) " * 70)
        self.assertEqual(65, len(bodies))
        self.assertEqual(6, bodies[-1].count("\n") + 1)

    def test_a_readonly_agent_cannot_hide_a_push_behind_the_cap(self):
        command = f"echo {self.FILLER}$(git push origin main)"
        code, err = run_profile("handle_readonly", command)
        self.assertEqual(2, code, err)


if __name__ == "__main__":
    unittest.main()

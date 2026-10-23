"""task-0153: the UserPromptSubmit reminder that runs wait for the User.

In-process tests cover the text; the subprocess tests run the real script against a sandbox
project, because the hook's contract is stdout bytes, exit status and elapsed time."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import needs_you_reminder
import state
import tmproot

SCRIPT = TOOLS_DIR / "hooks" / "needs_you_reminder.py"
TIME_BOUND_SECONDS = 2.0  # generous: bare interpreter start is about 0.06 s on the dev host


def waiting_run(task, kind="plan", lane="default", since="2026-10-22T10:00:00Z"):
    return {"task": task, "lane": lane, "kind": kind, "stage": "plan-approval", "since": since}


class RenderTest(unittest.TestCase):

    def test_nothing_waiting_renders_nothing(self):
        self.assertEqual("", needs_you_reminder.render([]))

    def test_the_header_says_it_is_a_harness_reminder_and_asks_for_a_card(self):
        text = needs_you_reminder.render([waiting_run("task-0128")])
        self.assertIn("Harness pipeline reminder", text)
        self.assertIn("not typed by the User", text)
        self.assertIn("AskUserQuestion card", text)
        self.assertIn("already answers the checkpoint", text)
        self.assertIn("1 run(s) wait", text)

    def test_one_line_per_run_with_task_lane_checkpoint_and_age(self):
        text = needs_you_reminder.render(
            [waiting_run("task-0128"), waiting_run("task-0129", "unblock", "docs")])
        self.assertIn("- task-0128 | lane default | plan approval | waiting since 2026-10-22T10:00:00Z", text)
        self.assertIn("- task-0129 | lane docs | unblock | waiting since", text)

    def test_more_than_the_cap_collapses_into_a_count(self):
        runs = [waiting_run(f"task-{n:04d}") for n in range(8)]
        text = needs_you_reminder.render(runs)
        self.assertEqual(needs_you_reminder.MAX_RUNS, text.count("\n- task-"))
        self.assertTrue(text.endswith("+3 more"))
        self.assertIn("8 run(s) wait", text)

    def test_the_text_is_ascii_and_within_the_byte_cap(self):
        runs = [waiting_run("task-é" + "x" * 300, lane="l" * 32) for _ in range(5)]
        text = needs_you_reminder.render(runs)
        self.assertTrue(text.isascii())
        self.assertLessEqual(len(text.encode("ascii")), needs_you_reminder.MAX_BYTES)

    def test_the_same_input_gives_the_same_bytes(self):
        runs = [waiting_run("task-0128"), waiting_run("task-0129", "commit")]
        self.assertEqual(needs_you_reminder.render(runs), needs_you_reminder.render(list(runs)))


class ScriptTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "needs_you_")
        self.state_dir = self.root / ".agentry" / "state"
        self.state_dir.mkdir(parents=True)

    def add_run(self, db_name, task, stage="plan-approval", status="in_progress", awaiting=""):
        conn = sqlite3.connect(self.state_dir / db_name)
        try:
            state.init_db(conn)
            conn.execute("INSERT INTO runs (task, type, stage, stage_status, awaiting_human, pipeline, "
                         "updated) VALUES (?, 'feature', ?, ?, ?, 'build', '2026-10-22T10:00:00Z')",
                         (task, stage, status, awaiting))
            conn.commit()
        finally:
            conn.close()

    def invoke(self, payload=b'{"prompt": "hi"}', env_extra=None, drop=()):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), **(env_extra or {})}
        for name in drop:
            env.pop(name, None)
        started = time.monotonic()
        done = subprocess.run([sys.executable, str(SCRIPT)], input=payload, env=env,
                              capture_output=True, timeout=30)
        done.stdout, done.stderr = done.stdout.decode("ascii"), done.stderr.decode("utf-8")
        return done, time.monotonic() - started

    def test_a_utf8_cyrillic_prompt_does_not_silence_the_reminder(self):
        # Bytes 0x81, 0x8D, 0x8F, 0x90 are undefined in cp1252, the locale codec of a Windows host.
        payload = json.dumps({"prompt": "\u0401\u044d\u044f \u0410 \u0434\u0430\u0432\u0430\u0439"},
                             ensure_ascii=False).encode("utf-8")
        self.assertTrue(any(b in payload for b in (b"\x81", b"\x8d", b"\x8f", b"\x90")))
        self.add_run("run.db", "task-0128", awaiting="plan")
        done, _ = self.invoke(payload=payload, env_extra={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
        self.assertEqual((0, ""), (done.returncode, done.stderr))
        self.assertIn("task-0128", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_a_supervisor_session_gets_no_reminder(self):
        self.add_run("run.db", "task-0128", awaiting="plan")
        done, _ = self.invoke(env_extra={needs_you_reminder.UNATTENDED_ENV: "1"})
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))

    def test_an_unset_project_dir_is_exit_zero_with_one_stderr_line_never_exit_two(self):
        done, _ = self.invoke(drop=("CLAUDE_PROJECT_DIR",))
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        self.assertIn("[needs-you-reminder] SystemExit", done.stderr)

    def test_the_unattended_variable_name_is_the_pretool_gates(self):
        import pretool_gate

        self.assertEqual(pretool_gate.UNATTENDED_ENV, needs_you_reminder.UNATTENDED_ENV)

    def test_a_waiting_run_in_any_lane_is_delivered_in_the_envelope_within_the_time_bound(self):
        self.add_run("run.db", "task-0100", stage="implement")
        self.add_run("run.docs.db", "task-0128", awaiting="plan")
        done, elapsed = self.invoke()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stderr)
        self.assertTrue(done.stdout.isascii())
        out = json.loads(done.stdout)["hookSpecificOutput"]
        self.assertEqual("UserPromptSubmit", out["hookEventName"])
        self.assertIn("task-0128 | lane docs | plan approval", out["additionalContext"])
        self.assertNotIn("task-0100", out["additionalContext"])
        self.assertLess(elapsed, TIME_BOUND_SECONDS)

    def test_nothing_waiting_prints_nothing(self):
        self.add_run("run.db", "task-0100", stage="implement")
        self.add_run("run.db", "task-0101", awaiting="plan", status="paused")
        done, _ = self.invoke()
        self.assertEqual((0, ""), (done.returncode, done.stdout))

    def test_the_output_is_byte_stable_across_two_runs(self):
        self.add_run("run.db", "task-0128", awaiting="plan")
        self.assertEqual(self.invoke()[0].stdout, self.invoke()[0].stdout)

    def test_a_corrupt_store_fails_open_without_noise(self):
        (self.state_dir / "run.db").write_bytes(b"garbage" * 100)
        done, _ = self.invoke()
        self.assertEqual((0, ""), (done.returncode, done.stdout))

    def test_a_crash_inside_the_hook_is_one_stderr_line_and_exit_zero(self):
        hooks_dir = str(TOOLS_DIR / "hooks")
        code = (f"import sys; sys.path.insert(0, {hooks_dir!r}); "
                "import needs_you_reminder as n, waiting; "
                "waiting.waiting_runs = lambda *a, **k: 1 / 0; raise SystemExit(n.main())")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        done = subprocess.run([sys.executable, "-c", code], input="{}", env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        self.assertTrue(done.stderr.startswith("[needs-you-reminder] ZeroDivisionError"), done.stderr)

    def test_the_hook_writes_nothing(self):
        self.add_run("run.db", "task-0128", awaiting="plan")
        before = {p.name: p.read_bytes() for p in self.state_dir.iterdir()}
        self.invoke()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state_dir.iterdir()})


class RegistrationTest(unittest.TestCase):

    def test_hooks_json_registers_it_on_user_prompt_submit_with_a_five_second_timeout(self):
        registered = json.loads((TOOLS_DIR.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entries = [h for g in registered["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertEqual(1, len(entries))
        self.assertIn("tools/hooks/needs_you_reminder.py", entries[0]["command"])
        self.assertEqual(5, entries[0]["timeout"])


if __name__ == "__main__":
    unittest.main()

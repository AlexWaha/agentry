#!/usr/bin/env python3
"""Deterministic exit-gate evaluator.

Runs a stage's configured exit-gate command and compares the exit code to the
expected value. This is the ONLY thing allowed to declare a stage's gate passed -
the model asserting "tests are green" does not advance the pipeline; this does.

CLI:
    python gate.py --task task-0007            # run current stage's gate
    python gate.py --stage test                # run a named stage's gate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

import state

PLACEHOLDER = "{{"
GATE_TIMEOUT = 900
OUTPUT_TAIL_BYTES = 4000
KILL_WAIT = 10
# The task id is spliced into a shell command, so only the registered shape passes.
TASK_ID_RE = re.compile(r"task-[0-9]+")


def taskkill_path() -> str:
    """taskkill by its absolute path: a bare name is looked up through PATH and the
    current directory, which a task can plant a program in."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill `proc` and everything it started, the grandchildren a test runner leaves
    behind included. On Windows `taskkill /T` walks the process tree from the pid, so
    the process group the command is started in plays no part; on POSIX the command
    runs in its own session precisely so `killpg` can reach them."""
    try:
        if os.name == "nt":
            subprocess.run([taskkill_path(), "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=KILL_WAIT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_command(cmd: str, cwd: str, timeout: float) -> tuple[int, str]:
    """(exit code, output tail) of `cmd`, bounded by `timeout` seconds.

    Output goes to a temp file, never a pipe: a grandchild that inherits a pipe's
    write end and outlives the command keeps the reader blocked past any timeout
    (subprocess.run waits for EOF after the kill). A file has no EOF to wait for,
    so the call returns when the command does. On timeout the whole process tree
    is killed and the exit code is 124."""
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    fd, path = tempfile.mkstemp(prefix="gaterun-", suffix=".log")
    code = 124
    note = ""
    try:
        with os.fdopen(fd, "wb") as sink:
            proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=sink,
                                    stderr=subprocess.STDOUT, **group)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                note = f"gate command timed out after {timeout:g}s"
                try:
                    proc.wait(timeout=KILL_WAIT)
                except subprocess.TimeoutExpired:
                    note += " (and did not exit when killed)"
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - OUTPUT_TAIL_BYTES))
            # Decoded as UTF-8 with replacement: on Windows the default text mode
            # decodes with the locale codepage (cp1252), which crashes on the UTF-8
            # output modern test runners emit (vitest check marks, box-drawing). Never
            # let a decode error masquerade as a gate failure.
            tail = log.read().decode("utf-8", errors="replace")
        return code, (f"{tail}\n{note}" if note else tail)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass  # a surviving grandchild still holds it open; the temp dir keeps it


def run_gate(pipeline: dict, stage_name: str, task: str | None = None,
             which: str = state.BUILD) -> dict:
    """Run the exit gate for a stage. Returns a result dict:
    {stage, has_gate, configured, cmd, exit, passed, output}."""
    stage_def = state.get_stage(pipeline, stage_name, which) or {}
    gate = stage_def.get("exit_gate")
    if not gate or not gate.get("cmd"):
        # Stages without a command gate (ready, done) auto-pass the command check.
        return {"stage": stage_name, "has_gate": False, "configured": True,
                "cmd": None, "exit": 0, "passed": True, "output": ""}

    if task and not TASK_ID_RE.fullmatch(task):
        return {"stage": stage_name, "has_gate": True, "configured": False,
                "cmd": str(gate["cmd"]), "exit": None, "passed": False,
                "output": f"refusing task id {task!r}: it must look like task-0123"}
    cmd = str(gate["cmd"]).replace("{task}", task or "")
    if PLACEHOLDER in cmd:
        return {"stage": stage_name, "has_gate": True, "configured": False,
                "cmd": cmd, "exit": None, "passed": False,
                "output": "exit-gate command still contains placeholders - run onboarding"}

    # Per-stage cwd for multi-repo workspaces (backend/, frontend/ as separate
    # repos): an exit_gate may set "cwd" to run inside its sub-repo.
    cwd = gate.get("cwd") or pipeline.get("cwd") or "."
    expect = gate.get("expect_exit", 0)
    started = time.monotonic()
    try:
        exit_code, output = run_command(
            cmd, str(state.ROOT / cwd if cwd != "." else state.ROOT), GATE_TIMEOUT)
    except OSError as exc:
        exit_code, output = 127, f"gate command failed to start: {exc}"

    passed = exit_code == expect
    if task:
        # Journal row, not run-store state: nothing reads it back to decide, and a
        # failed append never changes the result returned below.
        state.emit_gate(task, stage_name, which, cmd, exit_code, passed,
                        int((time.monotonic() - started) * 1000))
    return {"stage": stage_name, "has_gate": True, "configured": True,
            "cmd": cmd, "exit": exit_code, "passed": passed, "output": output}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run a stage exit gate")
    parser.add_argument("--task")
    parser.add_argument("--stage")
    args = parser.parse_args()

    pipeline = state.load_pipeline()
    stage_name = args.stage
    if not stage_name and args.task:
        conn = state.connect()
        run = state.get_run(conn, args.task)
        conn.close()
        if run:
            stage_name = run["stage"]
    if not stage_name:
        sys.stderr.write("need --stage or --task with an active run\n")
        return 2

    result = run_gate(pipeline, stage_name, args.task)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())

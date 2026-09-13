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
import subprocess
import sys

import state

PLACEHOLDER = "{{"


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

    cmd = str(gate["cmd"]).replace("{task}", task or "")
    if PLACEHOLDER in cmd:
        return {"stage": stage_name, "has_gate": True, "configured": False,
                "cmd": cmd, "exit": None, "passed": False,
                "output": "exit-gate command still contains placeholders - run onboarding"}

    # Per-stage cwd for multi-repo workspaces (backend/, frontend/ as separate
    # repos): an exit_gate may set "cwd" to run inside its sub-repo.
    cwd = gate.get("cwd") or pipeline.get("cwd") or "."
    expect = gate.get("expect_exit", 0)
    try:
        # Force UTF-8 decoding with replacement: on Windows the default text mode
        # decodes with the locale codepage (cp1252), which crashes on the UTF-8
        # output modern test runners emit (vitest check marks, box-drawing). Never
        # let a decode error masquerade as a gate failure.
        proc = subprocess.run(
            cmd, shell=True, cwd=str(state.ROOT / cwd if cwd != "." else state.ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
        exit_code = proc.returncode
        output = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired:
        exit_code, output = 124, "gate command timed out after 900s"
    except OSError as exc:
        exit_code, output = 127, f"gate command failed to start: {exc}"

    passed = exit_code == expect
    if task:
        conn = state.connect()
        state.log_gate(conn, task, stage_name, cmd, exit_code)
        conn.close()
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

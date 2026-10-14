# Plugin layout evidence: AC3 greps and AC5 hook execution

**Date:** 2026-10-14
**Author:** devops-engineer (task-0038, subtask S7)
**Status:** Draft
**Version:** 1.0

---

## Table of contents

- [Summary](#summary)
- [Method](#method)
- [AC3: crossed-reference greps](#ac3-crossed-reference-greps)
- [AC3: test_plugin_layout](#ac3-test_plugin_layout)
- [AC5: every hook executed once](#ac5-every-hook-executed-once)
- [Negative controls](#negative-controls)
- [claude plugin validate](#claude-plugin-validate)
- [Findings for the reviewer](#findings-for-the-reviewer)
- [Version history](#version-history)

---

## Summary

| Check | Result |
|---|---|
| AC5, hooks executed, exit 0 | 46 of 46 (plus 2 supplementary Edit-payload runs, 2 of 2) |
| `python -m unittest tests.test_plugin_layout` | 17 tests, OK |
| `claude plugin validate plugin/` (2.1.299) | passed, 1 warning (no `author` in `plugin.json`) |
| AC3 direction A (plugin never names project data) | literal greps are NOT empty: 1 planted-offender test string and 3 lines in `tools/setup/`, both listed under Findings |
| AC3 direction B (project never names a plugin path) | empty on the scope `test_plugin_layout` pins; NOT empty on `pipeline.json`, `stack.md`, `settings.json` `statusLine` and `orchestration.md`, all deliberate interim references, listed under Findings |

State of the tree: branch `feature/task-0038`, HEAD `c131f43`, work uncommitted in
the index and working tree. Host: Windows 11, Git Bash, Python 3.12.4, Claude Code
2.1.299.

## Method

- Greps are plain `grep -rnE` from `E:/Personal/Agenty/src` (no `rg` on this host).
  The patterns are the ones `plugin/tools/tests/test_plugin_layout.py` pins
  (`PLUGIN_TO_PROJECT_TEXT`, `PLUGIN_TO_PROJECT_CODE`, `OLD_PLUGIN_LOCATIONS`,
  `NEW_PLUGIN_LOCATIONS`), translated to ERE, run over a wider scope than the test
  uses so that nothing is hidden by the test's exemptions.
- Hook execution is `plugin/hooks/hooks.json` read as shipped. Each command is run
  once through Git Bash (`bash -c`), as Claude Code does on this host, with
  `CLAUDE_PLUGIN_ROOT=E:/Personal/Agenty/src/plugin`, `CLAUDE_PROJECT_DIR` and cwd
  set to the scratch project `E:/Personal/Agenty/tmp/s7-project`,
  `CLAUDE_ENV_FILE` set to a scratch file, and one JSON payload on stdin. The live
  workspace `E:/Personal/Agenty` was never pointed at.
- The scratch project holds a copy of `src/.agentry/pipeline.json` with
  `supervisor.enabled=false` (so `session_start` and `supervisor.py --ensure-running`
  start no daemon), a copy of `src/.claude/agents` (31 files) and `src/.claude/rules`,
  an empty `.agentry/state/`, and `docs/example.txt`.
- The scratch tree was built by a Python script (`tmp/s7-runner/run_hooks.py`), not
  by shell commands: the live `agent_gate` denies any shell command whose text
  names `.agentry/state`, `.agentry/pipeline.json` or `.claude/rules`, even under
  `tmp/`, so a shell `cp` into the scratch tree was refused. Nothing outside `tmp/`
  was written by that script.
- Payloads, one per event: SessionStart `{"source": "startup"}`; PreToolUse
  `Bash` `echo hello` (supplementary: `Edit` of `docs/example.txt`); PostToolUse
  `Bash` with a `tool_response`, and `Edit` of `docs/example.txt`; Stop
  `{"stop_hook_active": false}`; SubagentStart with `agent_id` and `agent_type`
  (`devops-engineer` wherever the entry's matcher lists it or it has no matcher,
  otherwise the first name in the matcher); SubagentStop with `agent_type=devops-engineer` and
  `last_assistant_message`; PreCompact `{"trigger": "manual"}`. Every payload also
  carries `session_id`, `transcript_path`, `cwd` and `hook_event_name`.
- "First line" is the first non-empty line of stdout, cut at 90 characters. An
  empty stdout is shown as `(none)`; stderr was empty in all 48 runs.

## AC3: crossed-reference greps

### Direction A: plugin code and data naming a project path

Pattern A1 (text, whole `plugin/` tree except the test that plants offenders):

```
$ grep -rnE '\$\{?(CLAUDE_PLUGIN_ROOT|AGENTRY_ROOT)\}?/(\.\.|\.agentry|\.claude|rules/|agents/|settings|CLAUDE\.md)' plugin --exclude-dir=__pycache__ --exclude=test_plugin_layout.py
plugin/tools/tests/test_paths.py:156:        for command in ('python "$AGENTRY_ROOT/../evil.py"', "python docs/memory.py --stats"):
exit=0
```

The one hit is a test fixture: `test_paths.py` feeds `$AGENTRY_ROOT/../evil.py` to
the resolver to prove it is refused. It is not a reference.

Pattern A2 (Python, runtime code, `tools/tests/` excluded):

```
$ grep -rnE '(PLUGIN_ROOT|plugin_root)[^[:space:]]*.*(\.agentry|\.claude)|(\.agentry|\.claude).*(PLUGIN_ROOT|plugin_root)|PLUGIN_ROOT\.parent|PLUGIN_ROOT[[:space:]]*/[[:space:]]*["'\'']\.\.["'\'']' plugin --include='*.py' --exclude-dir=__pycache__ --exclude-dir=tests
plugin/tools/setup/apply_optimization.py:470:    parser.add_argument("--source", default=str(paths.PLUGIN_ROOT.parent / ".claude"),
plugin/tools/setup/fleet_rollout.py:48:UNIVERSAL_ROOT = paths.PLUGIN_ROOT.parent      # <universal>, the checkout holding plugin/
plugin/tools/setup/rebuild_pipeline.py:39:SRC_PIPELINE = paths.PLUGIN_ROOT.parent / ".claude" / "pipeline.json"
exit=0
```

Three hits, all in `tools/setup/`. They go from the plugin root up to the repository
and into its `.claude/`. `test_plugin_layout.py` exempts `tools/setup/` by name
(`RUNTIME_EXEMPT`, plan C10: the setup tools turn a source checkout into a project
and are moved verbatim; whether they stay in the plugin is decided later). See
Findings, item 1.

Pattern A3 (every `parents[N]` in runtime code; the bound is the file's depth
below `plugin/`):

```
$ grep -rnE '\.parents\[[0-9]+\]' plugin --include=*.py --exclude-dir=__pycache__ --exclude-dir=tests
plugin/tools/hooks/dangerous_patterns.py:27:PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
plugin/tools/hooks/inject_rules.py:69:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/hooks/main_thread_rules.py:59:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/hooks/refusal_notice.py:24:sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
plugin/tools/hooks/session_start.py:55:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/hooks/session_start.py:59:TOOLS_DIR = HERE.parents[1]           # .../plugin/tools
plugin/tools/hooks/status_line.py:29:sys.path.insert(0, str(HERE.parents[1] / "pipeline"))
plugin/tools/memory/codebase_sync.py:28:sys.path.insert(0, str(HERE.parents[1] / "pipeline"))
plugin/tools/memory/inject.py:74:sys.path.insert(0, str(HERE.parents[1] / "pipeline"))
plugin/tools/memory/memory.py:60:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/memory/update.py:42:sys.path.insert(0, str(HERE.parents[1] / "pipeline"))
plugin/tools/paths.py:46:PLUGIN_ROOT = Path(__file__).resolve().parents[1]
plugin/tools/pipeline/advance.py:115:        p = str(Path(__file__).resolve().parents[1] / "memory")
plugin/tools/pipeline/events.py:54:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/pipeline/state.py:33:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/pipeline/stop_gate.py:797:        p = str(Path(__file__).resolve().parents[1] / "memory")
plugin/tools/setup/apply_optimization.py:48:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/setup/fleet_rollout.py:44:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/setup/rebuild_pipeline.py:35:sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plugin/tools/setup/repo_scan.py:52:sys.path.insert(0, str(HERE.parents[1] / "pipeline"))
```

Every file sits at `plugin/tools/<dir>/x.py` (or `plugin/tools/paths.py`) and uses
`parents[1]`, which is `plugin/tools` (or `plugin/` for `paths.py`). None climbs
past the plugin root.

`hooks.json` and the skills are covered by A1 (whole tree): no hit. `hooks.json`
carries no `CLAUDE_PROJECT_DIR`, `.claude` or `.agentry` in any command (also pinned
by `test_no_hook_command_resolves_through_the_project_root`).

### Direction B: project files naming a plugin path

Pattern B1, the scope `test_plugin_layout` pins (rules except `orchestration.md`,
agents, both `CLAUDE.md`):

```
$ grep -nE '\.claude/(tools|hooks)\b|\.claude/skills/([^v]|v[^e])' $(ls .claude/rules/*.md | grep -v orchestration.md) .claude/agents/*.md .claude/CLAUDE.md CLAUDE.md
exit=1
```

Empty (grep exit 1 is "no match"). `grep -c '"hooks"' .claude/settings.json` prints
`0`: hook registration is gone from the project settings.

Pattern B2, `${CLAUDE_PLUGIN_ROOT}` or a `plugin/...` path inside agents and settings
(`.claude/agents`, `.claude/settings.json`): no output either; the B2 hits below are
all in rules, `pipeline.json` and the overlay.

Pattern B1, widened to everything on the project side (the part the test does not
cover):

```
$ grep -rnE '\.claude/(tools|hooks)\b|\.claude/skills/([^v]|v[^e])' .claude/rules .claude/agents .claude/CLAUDE.md CLAUDE.md .claude/settings.json .agentry/project .agentry/pipeline.json
.claude/rules/orchestration.md:751:   `python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan`
.claude/rules/orchestration.md:753:   `python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}`. Verify
.claude/rules/orchestration.md:967:- **Live harness, every non-orchestrator writer.** `<project root>/.claude/tools/**`,
.claude/rules/orchestration.md:968:  `.claude/rules/**`, `.claude/skills/**`, `.claude/commands/**`,
.claude/rules/orchestration.md:982:  is `python` running one of `ALLOWED_SCRIPTS` from `<root>/.claude/tools/`.
.claude/rules/orchestration.md:1121:compileall` over `.claude/tools` and an import smoke test of every hook script
.claude/settings.json:43:    "command": "python \"$CLAUDE_PROJECT_DIR/.claude/tools/hooks/status_line.py\""
.agentry/project/architecture.md:244:the tools directory (then `.claude/tools/`, now `plugin/tools/`) on 2026-10-09 and the responsibilities from each script's
.agentry/project/stack.md:90:| `python -m unittest discover -s plugin/tools && python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}` | Test: full suite, then the UI-evidence check for the task. The `test` exit gate; `{task}` and `{root}` are filled in by the gate. `{root}/.claude/tools` is the frozen workspace-root copy, kept through task-0038 and replaced by `{plugin}` in task-0039 | `pipelines.build` stage `test` | 0 (3528 tests collected, 2026-10-14; the last full green run was measured at 1711 on 2026-10-12) |
.agentry/project/stack.md:135:- plan stage: `python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan`
.agentry/project/stack.md:136:- test stage, the evidence half: `python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}`
.agentry/project/stack.md:139:task-0038 leaves in place; task-0039 replaces `{root}/.claude/tools` with the
.agentry/pipeline.json:97:            "cmd": "python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan",
.agentry/pipeline.json:119:          "_comment": "ui_evidence: true appends 'python .claude/tools/pipeline/ui_evidence.py --task {task}' to the exit gate at onboarding, so UI-touching tasks require real-browser evidence in .agentry/state/evidence/<task>.json. Set false for API-only projects.",
.agentry/pipeline.json:120:          "exit_gate": { "cmd": "python -m unittest discover -s plugin/tools && python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}", "expect_exit": 0 }
.agentry/pipeline.json:159:            "cmd": "python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind brief",
.agentry/pipeline.json:169:            "cmd": "python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan",
.agentry/pipeline.json:193:            "cmd": "python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind spec",
```

Pattern B2 over rules, `pipeline.json` and the overlay (new-location names; these
are this repository's own gate commands and docs):

```
$ grep -rnE 'plugin/(tools|skills|hooks)\b|CLAUDE_PLUGIN_ROOT' .claude/rules .claude/agents .claude/settings.json .agentry/pipeline.json .agentry/project
(36 lines: orchestration.md 3, pipeline.json 4, architecture.md 12, project-context.md 1, stack.md 16; none in agents or settings.json)
```

The 36-line output is summarized here because it is the repository's own gate
commands (`python -m unittest discover -s plugin/tools`, `ruff check plugin/tools`)
and overlay prose describing the layout. Full text reproduces with the command above.

## AC3: test_plugin_layout

```
$ cd src/plugin/tools && python -m unittest tests.test_plugin_layout
.................
----------------------------------------------------------------------
Ran 17 tests in 0.087s

OK
exit=0
```

The test module also plants one offender per pattern (`GrepSanityTest`), so an empty
result is a real result and not a pattern that matches nothing.

## AC5: every hook executed once

Each command below was run as `python "${CLAUDE_PLUGIN_ROOT}/<path>" <args>` (the
`rm` row verbatim). All 46 registered entries are rows 1 to 46; rows S1 and S2 are
supplementary runs of the two PreToolUse gates with an `Edit` payload.

Counts by event: SessionStart 20 (1 `session_start`, 1 `supervisor`, 17 rule chunks,
1 index), PreToolUse 2, Stop 1, SubagentStart 18 (3 memory, 13 rule chunks, 1 index,
1 handoff), SubagentStop 1, PreCompact 1, PostToolUse 3. Total 46.

| # | Event | Matcher | Payload | Command (under `${CLAUDE_PLUGIN_ROOT}/`) | Exit | First line of output |
|---|---|---|---|---|---|---|
| 1 | SessionStart | `(none)` | startup | `tools/hooks/session_start.py` | 0 | (none) |
| 2 | SessionStart | `(none)` | startup | `tools/pipeline/supervisor.py --ensure-running` | 0 | (none) |
| 3 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 1` | 0 | ## Orchestrator-only rules, delivered to the main thread by $AGENTRY_ROOT/tools/hooks/main |
| 4 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 2` | 0 | --- .claude/rules/git-workflow.md (part 2 of 4) --- |
| 5 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 3` | 0 | --- .claude/rules/git-workflow.md (part 3 of 4) --- |
| 6 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 4` | 0 | --- .claude/rules/git-workflow.md (part 4 of 4) --- |
| 7 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 5` | 0 | --- .claude/rules/orchestration.md (part 1 of 13) --- |
| 8 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 6` | 0 | --- .claude/rules/orchestration.md (part 2 of 13) --- |
| 9 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 7` | 0 | --- .claude/rules/orchestration.md (part 3 of 13) --- |
| 10 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 8` | 0 | --- .claude/rules/orchestration.md (part 4 of 13) --- |
| 11 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 9` | 0 | --- .claude/rules/orchestration.md (part 5 of 13) --- |
| 12 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 10` | 0 | --- .claude/rules/orchestration.md (part 6 of 13) --- |
| 13 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 11` | 0 | --- .claude/rules/orchestration.md (part 7 of 13) --- |
| 14 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 12` | 0 | --- .claude/rules/orchestration.md (part 8 of 13) --- |
| 15 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 13` | 0 | --- .claude/rules/orchestration.md (part 9 of 13) --- |
| 16 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 14` | 0 | --- .claude/rules/orchestration.md (part 10 of 13) --- |
| 17 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 15` | 0 | --- .claude/rules/orchestration.md (part 11 of 13) --- |
| 18 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 16` | 0 | --- .claude/rules/orchestration.md (part 12 of 13) --- |
| 19 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --chunk 17` | 0 | --- .claude/rules/orchestration.md (part 13 of 13) --- |
| 20 | SessionStart | `(none)` | startup | `tools/hooks/main_thread_rules.py --index` | 0 | [main-thread-rules] delivered 17 of 17 chunks |
| 21 | PreToolUse | `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit` | Bash echo | `tools/pipeline/pretool_gate.py` | 0 | (none) |
| 22 | PreToolUse | `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit\|WebSearch\|WebFetch` | Bash echo | `tools/pipeline/agent_gate.py` | 0 | (none) |
| 23 | Stop | `(none)` | stop | `tools/pipeline/stop_gate.py` | 0 | (none) |
| 24 | SubagentStart | `architect\|product-manager\|sprint-prioritizer\|rapid-prototyper` | agent_type=architect | `tools/memory/inject.py --kinds module,pattern,lesson --limit 8` | 0 | (none) |
| 25 | SubagentStart | `spec-developer` | agent_type=spec-developer | `tools/memory/inject.py --kinds module,lesson --limit 8` | 0 | (none) |
| 26 | SubagentStart | `senior-backend-dev\|senior-frontend-dev\|data-engineer\|devops-engineer\|qa-engineer\|reviewer\|security-engineer` | agent_type=devops-engineer | `tools/memory/inject.py --kinds lesson,pattern --limit 6` | 0 | (none) |
| 27 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 1` | 0 | {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "[agent-rul |
| 28 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 2` | 0 | {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "[agent-rul |
| 29 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 3` | 0 | {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "[agent-rul |
| 30 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 4` | 0 | (none) |
| 31 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 5` | 0 | (none) |
| 32 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 6` | 0 | (none) |
| 33 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 7` | 0 | (none) |
| 34 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 8` | 0 | (none) |
| 35 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 9` | 0 | (none) |
| 36 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 10` | 0 | (none) |
| 37 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 11` | 0 | (none) |
| 38 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 12` | 0 | (none) |
| 39 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --chunk 13` | 0 | (none) |
| 40 | SubagentStart | `(none)` | agent_type=devops-engineer | `tools/hooks/inject_rules.py --index` | 0 | {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "[agent-rul |
| 41 | SubagentStart | `architect\|product-manager\|sprint-prioritizer\|rapid-prototyper\|spec-developer\|senior-backend-dev\|senior-frontend-dev\|data-engineer\|devops-engineer\|qa-engineer\|reviewer\|security-engineer` | agent_type=devops-engineer | `tools/pipeline/handoff.py --inject` | 0 | (none) |
| 42 | SubagentStop | `(none)` | agent_type=devops-engineer | `tools/hooks/subagent_stop.py` | 0 | Subagent devops-engineer finished. If its report contains a mistake, surprise, or User cor |
| 43 | PreCompact | `(none)` | manual | `tools/pipeline/state.py --resume` | 0 | (none) |
| 44 | PostToolUse | `Bash` | Bash echo | `rm -f nul NUL` | 0 | (none) |
| 45 | PostToolUse | `Bash` | Bash echo | `tools/hooks/refusal_notice.py` | 0 | (none) |
| 46 | PostToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit` | Edit docs/example.txt | `tools/hooks/dangerous_patterns.py` | 0 | (none) |
| S1 | PreToolUse | `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit` | Edit docs/example.txt | `tools/pipeline/pretool_gate.py` | 0 | (none) |
| S2 | PreToolUse | `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit\|WebSearch\|WebFetch` | Edit docs/example.txt | `tools/pipeline/agent_gate.py` | 0 | (none) |

Result: **46 of 46 registered hooks exited 0**, plus 2 of
2 supplementary runs. No timeout, no stderr output. The slowest entry is
`session_start.py` (about 10 s, cause not analysed; limit 60 s); every other
entry finished in under 0.2 s against limits of 5 to 20 s.

Side effects in the scratch project, all expected: `.agentry/state/run.db` and
`.agentry/state/supervisor.log` created (`supervisor is disabled in pipeline.json
(supervisor.enabled=false)`), and `env-file` holds the two exports `session_start`
writes:

```
export CLAUDE_PROJECT_DIR=E:/Personal/Agenty/tmp/s7-project
export AGENTRY_ROOT=E:/Personal/Agenty/src/plugin
```

### Negative controls

Exit 0 on an allowed payload says little if the gate would also exit 0 on
everything. The same plugin commands, run the same way against the scratch
project, were fed payloads that must be denied:

| Gate | Payload | Exit | Message (first 140 characters) |
|---|---|---|---|
| `agent_gate.py` | `devops-engineer` (profile `dev`, looked up from the scratch `.claude/agents/devops-engineer.md`) edits `.agentry/pipeline.json` | 2 | Dev agent: '.../tmp/s7-project/.agentry/pipeline.json' is under .agentry/state/, is .agentry/pipeline.json, is the live harness (the plugin root w |
| `agent_gate.py` | `reviewer` (profile `readonly`) edits `docs/example.txt` | 2 | This agent is read-only - it audits and reports, it does not modify files. Return findings to the orchestrator. |
| `pretool_gate.py` | main thread (no agent identity) runs `git commit -m x` | 2 | Not on a feature branch (no task-XXXX). Create the task branch first per .claude/rules/git-workflow.md. |

So the plugin-level `agent_gate` registration, with no `--profile` argument,
resolves the profile from the agent file in `${CLAUDE_PROJECT_DIR}`, and the gates
deny when they should.

## claude plugin validate

```
$ claude --version
2.1.299 (Claude Code)
$ claude plugin validate plugin/
Validating plugin manifest: E:\Personal\Agenty\src\plugin\.claude-plugin\plugin.json

⚠ Found 1 warning:

  ❯ author: No author information provided. Consider adding author details for plugin attribution

✔ Validation passed with warnings
exit=0
```

## Findings for the reviewer

1. **Direction A is not literally empty: `tools/setup/` reaches into the repository's
   `.claude/` (A2, 3 lines).** `apply_optimization.py:470`, `fleet_rollout.py:48`
   and `rebuild_pipeline.py:39` build `PLUGIN_ROOT.parent / ".claude"` paths. The
   test exempts the directory by name and plan C10 defers the decision whether
   `tools/setup/` stays in the plugin. The AC
   text "no crossed reference" is met for runtime code outside `tools/setup/`, and
   for `hooks.json` and the skills.
2. **Direction B is not literally empty outside the pinned scope (B1 widened).**
   Four places still name `.claude/tools`, all deliberate interim references to the
   frozen workspace-root copy, which stays live until task-0039 switches the harness
   to the plugin: `.agentry/pipeline.json` (6 lines, the `{root}/.claude/tools/pipeline/...`
   gate commands and one `_comment`), `.agentry/project/stack.md` (4 lines, which say
   so), `.claude/settings.json:43` (`statusLine`, a plugin cannot ship it, probe P5)
   and `.claude/rules/orchestration.md` (6 lines, the deploy runbook for the frozen
   copy, exempted in the test as `PROJECT_PROSE_EXEMPT`). `architecture.md:244` is a
   history sentence. `test_plugin_layout` does not scan `pipeline.json` or the
   overlay, so those 10 lines have no test; the reviewer decides whether AC3 accepts
   them until task-0039.
3. **Scratch build needed a Python script.** The live `agent_gate` denied a shell
   `cp`/`mkdir` into `tmp/s7-project/.agentry/state` and `.claude/rules` (the check
   matches the path text, not the real location). The orchestrator may want to
   narrow that check for `tmp/`; it is a false positive for sandboxes, which the
   test suite itself builds from Python.
4. `plugin.json` has no `author` (validator warning). Not an AC item.

## Version history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-14 | First draft: AC3 greps, test run, 46 hook executions, validate output. |

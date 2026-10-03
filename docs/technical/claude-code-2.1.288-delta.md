# Claude Code 2.1.270 - 2.1.288: what changed for this harness

**Date:** 2026-10-03
**Author:** technical-writer
**Status:** In Review
**Version:** 1.0

---

## Table of Contents

- [1. Stale claims in the harness](#1-stale-claims-in-the-harness)
- [2. Opportunities (new platform features)](#2-opportunities-new-platform-features)
- [3. Behavior changes to verify against the pipeline](#3-behavior-changes-to-verify-against-the-pipeline)
- [4. Measurements to redo on 2.1.288](#4-measurements-to-redo-on-21288)

---

Installed build on 2026-10-03: **2.1.288**. Most harness measurements cite
**2.1.269** (`inject_rules.py`, `memory/inject.py`, `stop_gate.py`,
`.claude/CLAUDE.md`). Source: the official CHANGELOG.md, versions 2.1.270
through 2.1.288, filtered for hooks, subagents, rules, skills, settings and
permissions. Entries for VS Code, cloud sessions, Claude Tag and Code Review are
left out.

Status column: **stale** = a harness claim is now wrong; **opportunity** = a new
platform feature could replace or simplify harness code; **check** = may affect
behavior, needs a measurement; **info** = good to know, no action.

## 1. Stale claims in the harness

| Version | Change | Harness impact | Status |
|---|---|---|---|
| 2.1.288 | Path-scoped `.claude/rules` and nested CLAUDE.md now load when Write or Edit touches a file in scope, not only on Read | `.claude/CLAUDE.md` says a nested CLAUDE.md "loads only when an agent reads a file inside the repo"; also `_onboarding.md` | stale |
| 2.1.288 | PreToolUse and PermissionRequest hooks: if matching fails or tool input cannot be serialized, the call is now BLOCKED | `orchestration.md` "All gates fail open" is true for our scripts' internals, but the platform now fails closed before our script runs | stale (wording) |
| 2.1.277 | `TaskOutput` tool removed; background output is read with Read on the output file | no reference found in the harness | info |

## 2. Opportunities (new platform features)

| Version | Feature | Where it fits | Status |
|---|---|---|---|
| 2.1.271 | `omitClaudeMd: true` in agent frontmatter: subagent runs without user/project/local CLAUDE.md (managed policy still loads) | the per-dispatch byte budget the harness fights with `claudeMdExcludes`; agents that get their rules through `inject_rules.py` could skip the root CLAUDE.md. Caveat: the core `@rules/` set rides on CLAUDE.md, so those agents would lose it unless `inject_rules.py` delivers it | opportunity |
| 2.1.281 | `"attribution": false` in `settings.json` hides all commit and PR attribution | the CEO's no-AI-attribution rule is currently enforced only by `pretool_gate.py` denying the trailer; the platform still injects a reminder asking for `Co-Authored-By`. Setting it removes the source. Older CLIs skip a settings file holding the boolean form | opportunity |
| 2.1.286 | A project or user skill named `verify` is run by Claude right before committing (except docs-only and tests-only commits) | `skills/health-check` is the pre-commit gate; a `verify` skill (or rename) gets the platform to call it at the right moment | opportunity |
| 2.1.283 | `/doctor prompt-audit` audits CLAUDE.md, skills, agents and commands for prompting patterns written for older models | 31 agents, 31 skills, 15+ rules: one run gives a cheap quality pass | opportunity |
| 2.1.288 | InstructionsLoaded hook now carries `agent_id` / `agent_type` when a subagent's file access loads a rule or nested CLAUDE.md | lets the harness measure which rules actually reach which agent instead of inferring it | opportunity |
| 2.1.287 | Claude Mods: plugins may now modify deeper behavior; built-in "You should know" side-agent mod | possible future home for status panes (pipeline state, busy marker) instead of SessionStart text. Not needed now | info |
| 2.1.277 | AGENTS.md is read when a project has no CLAUDE.md | template portability only | info |

## 3. Behavior changes to verify against the pipeline

| Version | Change | Why it matters here | Status |
|---|---|---|---|
| 2.1.285 / 2.1.288 | Background Bash/PowerShell commands now stop at their `timeout` (default 30 min, max 2 h), but since 2.1.288 only in unattended sessions (`-p`, SDK, CI) | supervisor-spawned unattended sessions: a long gate or test run in the background will be killed at 30 min | check |
| 2.1.288 | Mid-response API timeouts: subagents and `-p` sessions continue from the partial response | fewer lost dispatches; the HUNG/SLOW logic in `supervisor.py` may see fewer stalls | info |
| 2.1.288 | `CLAUDE_CODE_RETRY_WATCHDOG` unattended sessions give up after three stream timeouts instead of retrying for hours | supervisor relaunch logic should expect that exit | check |
| 2.1.288 | Dangerous `rm` inside `bash -c` / `sh -c` now prompts even in `bypassPermissions` | every agent runs `bypassPermissions`; such a prompt would surface to the CEO (see lesson `subagent-prompts-reach-the-ceo`) | check |
| 2.1.287 | Whole-tool `Bash` allow rules and allowing hooks now prompt for shell writes to files the file tools refuse (credential store) | same channel: a prompt in a subagent reaches the CEO | info |
| 2.1.285 | Synchronous hooks no longer hang while a background child holds their output open | related to the piped-suite hang family in `quality-standard.md`; that rule is about the test suite, still valid | info |
| 2.1.285 | Fork subagents run under the parent's permission mode and cannot exit plan mode | relevant if forks are dispatched from plan mode | info |
| 2.1.285 | In auto mode a subagent's run ends as soon as it hands its report back | fewer wasted turns per dispatch | info |
| 2.1.285 | `claude -p` with no permission mode on third-party providers or telemetry off starts in auto mode | the supervisor should pass `--permission-mode` explicitly | check |
| 2.1.280 | A finished subagent's report is no longer lost when the launching conversation compacts before it is read | removes one cause of "dispatch returned nothing" | info |
| 2.1.277 | Subagent results reach the main agent under a header with the result indented | report-format rules in `communication.md` still apply | info |
| 2.1.277 | SessionStart hook output no longer causes a full prompt-cache miss after `/clear` + resume | `main_thread_rules.py` chunks are cheaper on resume | info |
| 2.1.275 | `SubagentStop` hooks with a `matcher` no longer fire for subagents with an empty type | ours has no matcher: unaffected | info |
| 2.1.287 | A folder's CLAUDE.md is no longer attached a second time after resume or compaction; 2.1.286 same fix for worktree-isolated subagents | earlier byte measurements of dispatch cost may have counted a duplicate | check |
| 2.1.271 | Monitor watches always have a deadline (30 min, 10 in `-p`); `persistent` option removed | any Monitor-based watcher must re-arm | info |
| 2.1.288 | `idle_prompt` notification hooks no longer fire while background agents run | none | info |
| 2.1.284 | Sonnet 5.5 (`claude-sonnet-5-5`) is the default Sonnet: 1M context, $2/$10 per Mtok | agent `model:` choices for cheap roles | info |

## 4. Measurements to redo on 2.1.288

Each of these is a numeric claim taken from the 2.1.269 binary:

- hook stdout persisted to a file past 10,000 characters (`NEr`), which sets the
  chunk count of `main_thread_rules.py`;
- the `SubagentStart` payload carrying no prompt text and discarding plain
  stdout (`memory/inject.py`, task-0081);
- the `stop_gate.py` measurements at lines 120 and 187;
- `.claude/CLAUDE.md`: the rules directory walk at session start.

Note: the git repo is `src/`; `src/docs/technical/nested-claude-md.md` exists and
its audit table must be re-checked against the 2.1.288 Write/Edit loading change.

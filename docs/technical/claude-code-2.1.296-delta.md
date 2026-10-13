# Claude Code 2.1.296: re-measurement of the 2.1.269 platform claims

**Date:** 2026-10-13
**Author:** devops-engineer (task-0106)
**Status:** In Review
**Version:** 1.0

---

## Table of Contents

- [1. Why this document is named 2.1.296](#1-why-this-document-is-named-21296)
- [2. Method](#2-method)
- [3. Results at a glance](#3-results-at-a-glance)
- [4. The five measurements](#4-the-five-measurements)
- [5. omitClaudeMd](#5-omitclaudemd)
- [6. What was not measured](#6-what-was-not-measured)

---

## 1. Why this document is named 2.1.296

Task-0106 was written against 2.1.288 and asked for the results in section 4 of
`claude-code-2.1.288-delta.md`. The host now runs `2.1.296 (Claude Code)`
(`claude --version`, 2026-10-13), so every number below was taken on 2.1.296, and a
section labelled 2.1.288 would claim a build nobody measured. The 2.1.288 document
stays as what it is: the changelog audit of 2.1.270 to 2.1.288. Its section 4 now
points here.

## 2. Method

Two instruments, both run on the live host, both in scratch projects under
`E:/Personal/Agenty/tmp/t0106/` (outside the git repo):

- **The binary.** `claude.exe` is a Bun bundle; its JavaScript is readable with a
  memory-mapped regex scan (`tmp/t0106/binprobe.py`). Minified names change on every
  build (`Zhe` is now `gce`, `NEr` is now `PCs`), so every claim below is anchored on
  a string or a value, never on a variable name or an offset.
- **A live probe.** Headless `claude -p --model haiku` in a scratch project whose
  settings define a hook that prints a known size or dumps its stdin to a file. The
  result is read from the session transcript, not from what the model says about
  itself: since 2.1.296 the transcript stores structured attachments
  (`instructions` for every CLAUDE.md and rule file with its full content,
  `hook_additional_context`, `hook_success`, `nested_memory`), which makes a byte
  count exact. The `usage` of the first assistant message gives the context size in
  tokens.

The probe projects sit below `E:/Personal/Agenty`, so the live root `CLAUDE.md`,
`.claude/CLAUDE.md` and `.claude/rules/` load into them exactly as into a real
dispatch, and the scratch settings copy the live `claudeMdExcludes`. The user-level
`~/.claude/CLAUDE.md` also loaded (5,717 characters), which is why a probe model
once ran `rm -f nul NUL` unprompted.

## 3. Results at a glance

| # | Claim (2.1.269) | 2.1.296 | Result |
|---|---|---|---|
| 1 | Hook output over 10,000 characters is persisted; chunk count fixed by it | Same threshold (10,000, `<=` passes), same 2000-char preview; constant renamed | unchanged; 16 main-thread chunks and 13 rule chunks still right |
| 2 | `SubagentStart` payload has no prompt; only `additionalContext` in the envelope reaches the agent | Same fields, same envelope rule; plain stdout still dropped | unchanged |
| 3 | `stop_gate.py` claims: Stop payload, vendor block cap of 8 reset by tool use, sticky `stop_hook_active`, exit codes 0/1/2, `terminalSequence` allowlist | All hold; one detail added (OSC 9 progress form) | unchanged (one refinement) |
| 4 | `.claude/rules/` walk at session start, `@rules/` lines add nothing | Walk confirmed in the `instructions` attachment, no second copy | unchanged |
| 5 | Dispatch bytes may include a duplicate CLAUDE.md | 87,745 characters, 9 files, each once; none after resume | no duplicate on the paths measured |

## 4. The five measurements

### 4.1 Hook output persisted past 10,000 characters

Binary, 2026-10-13:

```
$ python -I binprobe.py '[,; ]PCs=[0-9e]+'
... var p0=50000,yEr=1e4,djn=4000,n6e=128000,Eue=500000;var w$e=4,ACs=400000,ujn=200000,VT=50,xCs=1073741824,PCs=1e4,hen=1e4,lMt=1e5; ...
$ python -I binprobe.py 'gce\(ms\.stdout|gce\([A-Za-z_.]+\.stdout'
... let Ks=await gce(Ro.stdout.trim(),_i,"stdout",{storageV5:dt});yield{message:Zt({type:"hook_success", ...
$ python -I binprobe.py 'gce\([^)]{0,80}additionalContext'
... let gr=await gce(zo.additionalContext,`${s}-${Ur}`,"additionalContext",{storageV5:dt}); ...
... async function gce(e,n,r,{threshold:s=PCs,storageV5:a}={}){if(e.length<=s)return e;let o=await _4(e,`hook-${n}-${r}`,...
... var nCe=2000 ...
```

So the threshold is `PCs = 1e4`, the test is `length <= threshold`, stdout and
`additionalContext` go through the same function, and the preview is `nCe = 2000`.

Live, SessionStart hooks that print exactly N characters (`tmp/t0106/p1b`):

```
hook A (N = 9999 + CRLF = 10000):  stdout len 10000 | content len 10000 | tail: ...xxxx SS-END-A
hook B (N = 10000 + CRLF = 10001): stdout len 10001 | content len 2284  | head: <persisted-output> Output too large (9.8KB). Full output saved to ...
```

10,000 arrives whole, 10,001 is replaced by a `<persisted-output>` block. The probe
script wrote through Python's text mode, so each newline cost one extra character on
Windows; the harness hooks do not (their stdout contains no carriage return, checked
on all 16 main-thread chunks).

Live, SubagentStart (`tmp/t0106/p1`): `additionalContext` of 120 and 9,900
characters reached the agent (markers `ENV-END-SMALL`, `ENV-END-9900` present in its
`hook_additional_context` attachment); one of 10,100 arrived as `Output too large
(9.9KB). Full output saved to ...hook-<id>-4-additionalContext.txt` plus a 2KB
preview and the marker `ENV-END-10100` was absent. A SessionStart hook's stdout
reached the main thread (`SS-END-9900` present) and did not reach the subagent.

Chunk counts on the current tree: `main_thread_rules.py --index` prints `delivered 16
of 16 chunks`; the largest chunk is 8,677 characters. `inject_rules.py` needs at most
13 chunks (reviewer) against 13 wired entries. Both unchanged.

### 4.2 SubagentStart payload and envelope

Binary:

```
$ python -I binprobe.py 'hook_event_name:"SubagentStart"'
... let D={...$d(e.session,se()),hook_event_name:"SubagentStart",agent_id:n,agent_type:r}; ...
```

Live: a SubagentStart hook that dumped stdin received exactly

```
session_id, transcript_path, cwd, prompt_id, agent_id, agent_type, hook_event_name
```

(`permission_mode` was absent for this agent, matching the `?` in the code comment).
No prompt, description or task field. In the same run a hook that wrote
`PLAIN-STDOUT-SUBAGENTSTART-MARK` to plain stdout left a `hook_success` record but
the marker was absent from the agent's `hook_additional_context`, and the agent
confirmed it was not in its context; the envelope hooks' markers were present.
Unchanged from 2.1.269 (task-0081).

### 4.3 The `stop_gate.py` measurements

Live (`tmp/t0106/p3-*`): a Stop hook that dumps stdin and exits with a chosen code,
one headless run each, 2026-10-13.

Payload, first and later Stops, key set identical:

```
session_id, transcript_path, cwd, prompt_id, permission_mode, effort, hook_event_name,
stop_hook_active, last_assistant_message, background_tasks, session_crons
```

No count field. Vendor cap, hook `exit2always` (blocks every Stop):

```
Stop hook #1 exit=2 ... #6 exit=2 (model then used the Read tool) #7 ... #15 exit=2
A hook blocked the turn from ending 9 consecutive times - overriding and ending turn.
```

Binary: `let Se=a.CLAUDE_CODE_STOP_HOOK_BLOCK_CAP??8;if(Se>0&&Oe>Se){...` and
`transition:{reason:"next_turn"}`. The override came on the ninth consecutive block;
15 hook runs happened because the Read after block 6 started the count again. That
is the claim in `stop_gate.py`, now observed. `stop_hook_active` was false on Stop 1
and true on Stops 2 to 15, including the nine after the Read.

Exit codes, one run each: `exit0` = success, no notification; `exit1` and a raised
traceback (exit 1) = `Stop hook error occurred - ctrl+o to see` (key
`stop-hook-error`), turn ends `end_turn`; `exit2` = blocks, stderr delivered as
`Stop hook feedback:`. The notification showed once in the fifteen-Stop run, so the
"exit 1 while `stop_hook_active` is true" variant could not be re-observed.

`terminalSequence` allowlist, binary: `rJn=new Set([0,1,2,9,99,777]),sJn=4096`,
`aJn(e){if(/^4;[0-4](;(100|\d{1,2})?)?$/.test(e))return!0;return!/^[\s\u180e\u200b]*[+-]?\p{Nd}/u.test(e)}`.
One refinement: an OSC 9 body may start with a digit when it is the `9;4` progress
form. None of the four events in `TERMINAL_EVENTS` uses it. The limit is 4096
characters, not bytes. `stop_gate.py` comments updated.

### 4.4 The `.claude/rules/` walk

Live, 2026-10-13, main thread and subagents of a project below the workspace root.
Without `claudeMdExcludes` the `instructions` attachment held 23 files: user
`CLAUDE.md`, root `CLAUDE.md`, `.claude/CLAUDE.md` and all 20 rule files, each once
(`tmp/t0106/p1`). With the live `claudeMdExcludes` it held 9: those three plus the six
core rules (`tmp/t0106/p4`, `files.py`):

```
User    C:/Users/AlexWaha/.claude/CLAUDE.md                       5717
Project E:/Personal/Agenty/CLAUDE.md                              6998
Project E:/Personal/Agenty/.claude/CLAUDE.md                     23774
Project E:/Personal/Agenty/.claude/rules/code-retrieval.md        2113
... communication.md 8741, pipeline.md 12370, quality-standard.md 14220,
    self-learning.md 8500, task-creation.md 5312
```

The walk happens at session start (the attachment precedes the first model turn),
climbs to ancestors of the working directory, and the `@rules/` lines in
`.claude/CLAUDE.md` add no second copy. Unchanged; the sentence in `.claude/CLAUDE.md`
is re-stamped. (The root `CLAUDE.md` read 10,886 characters in the first probe and
6,998 in the later one: the live file was edited between runs. Every comparison below
is inside one run.)

### 4.5 Per-dispatch bytes of CLAUDE.md, and the duplicate

A plain subagent (`tmp/t0106/p4`, `analyze.py`): one `instructions` attachment, 9
files, 87,745 characters, no path repeated. The main thread after `claude --resume`
still had exactly one `instructions` attachment of 87,745 characters, so resume did
not attach a second copy. The earlier cost numbers were therefore not inflated by a
duplicate on these two paths.

## 5. omitClaudeMd

Source: the agent schema in the 2.1.296 binary, "Run this agent without the user,
project and local CLAUDE.md instruction files when it runs as a subagent; managed
policy files are kept. ... No effect on the main session agent." The frontmatter
parser accepts `true` and `"true"`.

### 5.1 What the flag removes and keeps (measured)

With the flag the subagent has no `instructions` attachment at all: the user
`CLAUDE.md`, both project `CLAUDE.md` files and the whole rules walk are gone. The
hook-injected context (`inject_rules.py`, memory, handoff) is unaffected, and a
nested `CLAUDE.md` still loads when the agent reads a file beside it (a
`nested_memory` attachment, present for flagged and unflagged agents alike).

### 5.2 Bytes per dispatch

Same prompt, same stripped agent, only the flag toggled; the flagged copy declares
the six core rules in `rules:` so `inject_rules.py` delivers them. First request of
the subagent, input plus cache tokens (`tmp/t0106/p4`):

| Profile | Clone of | Without flag (tokens) | With flag (tokens) | Saved |
|---|---|---|---|---|
| dev | devops-engineer | 53,345 | 39,209 | 14,136 (26.5%) |
| docs | technical-writer | 49,445 | 35,309 | 14,136 (28.6%) |
| readonly | reviewer | 78,366 | 64,215 | 14,151 (18.1%) |

Bytes behind the tokens: CLAUDE.md-borne context without the flag is 87,745
characters (user 5,717, root 6,998, `.claude/CLAUDE.md` 23,774, six core rules
51,256); with the flag it is 0, and `inject_rules.py` delivers the core rules
instead (devops 29,087 to 80,810 injected characters, technical-writer 30,109 to
81,832, reviewer 103,449 to 155,150). The saving is the 36,489 characters of
CLAUDE.md files, about 14.1k tokens, the same for every agent.

### 5.3 Decision per profile

| Profile | Decision | Reason |
|---|---|---|
| dev | **no** | A dev agent has an unrestricted shell. The "no `gh`, no PR creation" rule reaches it only through the user-level `CLAUDE.md` and `git-workflow.md` (main thread only); no gate enforces it (`grep` of `pretool_gate.py` and `agent_gate.py` for `gh` finds nothing). The flag would drop it silently. Saving forgone: 14,136 tokens (26.5%) on devops-engineer. Revisit when that rule is a gate or part of the core set. |
| docs | **yes**, 16 of 17 agents | The docs profile is default-deny on Bash (`READ_ONLY_PROGRAMS`, no `gh`), so nothing the user file carries for shell use applies. Every rule the agent needs (core set plus its own `rules:`) still arrives. `seo-specialist` stays: its rules plus the core set need 14 chunks against 13 wired entries. |
| readonly | **yes**, 3 of 8 agents | Default-deny Bash. `reviewer` (19 chunks needed) and `architect` (exactly 13, no headroom) stay. The three agents whose hook line carries `--allow-script` (`performance-benchmarker`, `evidence-collector`, `accessibility-auditor`) stay too: every one lists `npx`, which installs and runs any package (`gh` included), so they are not shell-less, and the "no `gh`, no PR creation, no push" rule lives only in `CLAUDE.md`. `test_no_agent_with_allow_script_omits_claude_md` pins it. `evidence-collector` also remains the real-data sample of the silent path in `test_inject_rules.py`. |

Applied to 19 agents, listed in `OMIT_CLAUDE_MD_AGENTS` in `test_agent_frontmatter.py`.

Final configuration, measured on the real shipped agent files (hooks, skills and MCP
stripped), before the change on the HEAD versions and after on the working tree:

| Agent | CLAUDE.md characters | All six core rules injected | Chunks | First request tokens |
|---|---|---|---|---|
| technical-writer, before | 87,745 | no | 3 | 49,445 |
| technical-writer, after | 0 | yes | 10 | 35,304 |
| security-engineer, before | 87,745 | no | 4 | 50,660 |
| security-engineer, after | 0 | yes | 10 | 36,504 |
| devops-engineer (unflagged control) | 87,745 | no | 3 | 53,346 |

Proof in the suite: `test_inject_rules.py` (`test_an_agent_that_omits_claude_md_still_receives_every_core_rule`,
`test_a_real_dispatch_of_an_omitting_agent_delivers_the_core_set`) and
`test_agent_frontmatter.py` (`test_omit_claude_md_is_on_exactly_the_named_agents`,
`test_an_agent_that_omits_claude_md_declares_the_whole_core_set`). The existing
"no agent declares a core rule" test now exempts exactly the omitting agents.
`payload.py` stops charging an omitting agent for the three CLAUDE.md-borne rows.

### 5.4 Deploy

Agent definitions load at session start: a running Claude Code session keeps the old
frontmatter until it is restarted. No `settings.json` change is needed (the 19 agents
fit in the 13 wired chunks).

## 6. What was not measured

- A `isolation: worktree` subagent and a compaction: no duplicate check. The worktree
  case needs a committed scratch repository and `git commit` is denied to this agent.
  Resume was measured.
- Interactive (non-headless) sessions; all probes were `claude -p`.
- Concurrent subagents of the same type.
- The 2.1.288 changelog items outside the five (fail-closed PreToolUse matching, the
  `rm` in `bash -c` prompt, background command timeouts): not part of this task.

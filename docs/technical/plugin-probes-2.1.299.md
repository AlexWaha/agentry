# Plugin probes P0-P5 - Claude Code 2.1.299

Measured 2026-10-14 for task-0038, subtask S1 (devops-engineer). Host: Windows 11,
git-bash, `claude --version` = `2.1.299 (Claude Code)` (the version the plan
named, so this file keeps its name). The plan was written on 2.1.295.

Everything ran as headless `claude -p` against a scratch tree under
`E:/Personal/Agenty/tmp/plugin-probes/` (never the live `.claude/`, never a
write to `~/.claude` settings; Claude Code's own session and transcript files
aside). All runs used `--setting-sources project` so user settings and hooks
stayed out, `--plugin-dir <fixture>` for the plugin, `--model haiku` (sonnet for
the commit-trigger run), and `--debug-file` where the log mattered. `$B` below
is `E:/Personal/Agenty/tmp/plugin-probes`.

## Fixture

```
$B/plugin/                       fixture plugin, name "probe", 0.0.1
  .claude-plugin/plugin.json
  hooks/hooks.json               SessionStart x4 (quoted, unquoted, exec form, inject),
                                 PreToolUse *, SubagentStart x2, SubagentStop
  scripts/dump.py                writes one JSON file per hook call: argv, cwd, env subset,
                                 payload keys and values
  scripts/mcp_stub.py            stdio MCP server, one tool ping_<who>, launch marker file
  scripts/probe_env.py           writes env as seen directly and via subprocess shell=True
  scripts/inject.py              prints additionalContext / stdout tokens
  agents/pagent.md               permissionMode acceptEdits + hooks + mcpServers + profile
  skills/{probe-skill,probe-skill2,verify,probe-subst}/SKILL.md
$B/proj/                         scratch project, own git root, project settings hooks
  .claude/agents/{pagent-proj,sk-agent}.md   .claude/skills/proj-skill
$B/proj2/ proj3/ proj-verify/    variants (see P0 and P3 notes)
$B/out/<probe>/                  raw hook dumps, stream-json transcripts, debug logs
```

Setup quirks worth knowing before anyone re-runs this:

- A scratch project must be its own git root. `proj2` (no `.git`) loaded the 43
  skills of the live workspace `.claude/skills/` from the ancestor directory,
  so `.claude/skills/` is discovered up the directory tree.
- Git Bash rewrites an argument that is only a path: `claude -p "/probe-subst"`
  arrived as `C:/Program Files/Git/probe-subst`. Add trailing text
  (`"/probe-subst now"`) or set `MSYS_NO_PATHCONV=1`.
- `claude -p` prints `no stdin data received in 3s` on every run launched this
  way. Harmless; the redirect that would silence it is forbidden here.
- A plugin agent's `agent_type` is `probe:pagent`. A colon is not a legal
  Windows file-name character: my first `dump.py` named files after
  `agent_type` and created NTFS alternate data streams (`...-probe` with 0
  bytes visible, data in `:pagent-<pid>.json`). No Agentry tool builds a path
  from `agent_type` (grep of `.claude/tools`), so this is a note, not a defect.

## P0 - do plugin agents still ignore permissionMode, hooks and mcpServers?

**Verdict: YES, still ignored on 2.1.299. D6 is not triggered; agents stay
project-side as decided.**

Commands (parent session in default mode, so the agent's `acceptEdits` is
distinguishable; plugin agent and a control agent carry identical fields):

```
cd $B/proj2
claude -p --model haiku --setting-sources project --permission-mode default \
  --plugin-dir $B/plugin --agents $B/agents-cli.json --debug-file $B/out/p0-d/debug.log \
  --output-format stream-json --verbose \
  "Dispatch the agent 'probe:pagent', wait for it, then dispatch the agent 'cliagent' ..."
```

Raw evidence, `out/p0-d` (hook payload `permission_mode` per agent):

```
plugin-pretool-probe_pagent-400.json  probe:pagent  permission_mode=default      tool=Bash
agentfm-cli-agent-cliagent-*.json     cliagent      permission_mode=acceptEdits  tool=Bash   (agent-frontmatter hook fired)
plugin-pretool-cliagent-*.json        cliagent      permission_mode=acceptEdits  tool=mcp__pmcp__ping_cli_agent
```

Debug log, the three warnings for the plugin agent file:

```
[WARN] Plugin agent file ...\plugin\agents\pagent.md sets permissionMode, which is ignored for plugin agents. Use .claude/agents/ for this level of control.
[WARN] Plugin agent file ...\plugin\agents\pagent.md sets hooks, which is ignored for plugin agents. Use .claude/agents/ for this level of control.
[WARN] Plugin agent file ...\plugin\agents\pagent.md sets mcpServers, which is ignored for plugin agents. Use .claude/agents/ for this level of control.
```

| Field | Plugin agent `probe:pagent` | Honoring agent `cliagent` (`--agents` JSON) |
|---|---|---|
| permissionMode acceptEdits | payload says `default` | payload says `acceptEdits` |
| frontmatter `hooks:` | no `agentfm-*` marker file | marker file written |
| `mcpServers:` | reply `NO-PING-TOOL`, no `mcp-launch-*` file | tool `mcp__pmcp__ping_cli_agent` present, launch marker written; under a bypass parent (`out/p0-c`) it returned `pong-from-cli-agent` |

Two measured side findings that bear on the existing harness, found while
building the control:

1. **Project agents also lose their frontmatter hooks in an untrusted folder.**
   `out/p0-default-parent/debug.log`: `[ERROR] Skipping frontmatter hooks for
   agent 'pagent-proj': the folder its definition file came from is not trusted
   (source: projectSettings)`. Trust is keyed on the exact folder:
   `proj2` under the trusted `E:/Personal/Agenty` was refused, and
   `hasTrustDialogAccepted` is set per project key in `~/.claude.json` (read
   only, I did not change it). In that state the project agent's `permissionMode`
   and `mcpServers` were also not applied (`NO-PING-TOOL`). A real project
   agent could therefore not be the control; I used a CLI-defined agent
   (`--agents`), which honors all three. Settings-level hooks still fired in the
   same untrusted folder. Consequence: `agent_gate` carried only in agent
   frontmatter silently vanishes for a headless run in a folder that was never
   trusted interactively. The live workspace root is trusted.
2. **`permissionMode: bypassPermissions` on an agent is only honored under a
   bypass parent.** `[WARN] Subagent declared permissionMode bypassPermissions
   is not authorized by this session (parent not in bypass mode, bypass
   policy-disabled, session --restricted, or not a contained no-internet
   environment); keeping parent mode 'default'.` Conversely under a bypass
   parent a child `permissionMode: plan` did not downgrade (payload stayed
   `bypassPermissions`, n=1). `rules/communication.md` ("every agent sets
   bypassPermissions") holds on the live host only because the session itself
   is in bypass mode.

Meaning for S2-S7: none of the plan changes. Agents stay in `src/.claude/agents/`.
The plugin-level hooks that replace the agent `hooks:` blocks fire for every
agent regardless of that trust state (P1), which removes the silent-drop hole
in finding 1.

## P1 - subagent identity in the PreToolUse payload; unknown `profile:` key

**Verdict: HOLDS on 2.1.299 for foreground and background dispatch, built-in and
plugin agents. An unknown `profile:` key loads and runs; the warning is in the
debug log only.**

Command (`out/p1`, plugin hooks, `--allowedTools "Bash(echo *)" "Glob"`):

```
claude -p --model haiku --setting-sources project --permission-mode default ... --plugin-dir $B/plugin \
  --agents $B/agents-cli.json --output-format stream-json --verbose \
  "1) Agent general-purpose, run_in_background false, run Bash 'echo fg-gp'. 2) Agent Explore,
   run_in_background false, Glob '*.md'. 3) Agent cliagent, run_in_background true, run Bash 'echo bg-cli'. ..."
```

Raw (plugin-level `PreToolUse *`, trimmed):

```
Explore          a6fe456a624895b36  default      Glob
general-purpose  aa389394ebcdac9d8  default      Bash  echo fg-gp
cliagent         a43803a63e33e2091  acceptEdits  Bash  echo bg-cli      (background)
main (no agent_id, no agent_type)   default      Agent
```

- `agent_id` and `agent_type` are present on every subagent call and absent on
  the main thread, as measured on 2.1.295 (`agent-identity-2.1.295.md`). The
  same `agent_id` appears in that agent's `SubagentStart`, `PreToolUse` and
  `SubagentStop` payloads. `SubagentStop` also carries `permission_mode`.
- Built-in types arrive bare (`general-purpose`, `Explore`); project and CLI
  agents arrive by their name (`pagent-proj`, `cliagent`); plugin agents arrive
  namespaced (`probe:pagent`).
- **Plugin-level hooks fire for all of them**, project agents and built-ins
  included, in the untrusted folder as well (`plugin-pretool-pagent-proj-*.json`
  exists in `out/p0-default-parent`).
- Dispatch default: in two runs where the prompt did not set
  `run_in_background`, the Agent tool returned `Async agent launched
  successfully` and the main thread ended its turn early. Setting
  `run_in_background: false` gave a synchronous dispatch.
- `profile: dev` in agent frontmatter (project file and plugin file alike):
  `[WARN] Agent file ...\pagent-proj.md has a frontmatter key Claude Code does
  not recognize or apply: "profile"` in `--debug-file` output only. The agent
  still appears in the init `agents` list and runs. Nothing is printed at normal
  verbosity, and `claude plugin validate <agents dir>` passes with no warning.

Meaning: S4 can read the profile from a `profile:` key; `agent_type` is
sufficient to find the agent file for project agents. Decision D3 stands as
written: built-ins reach a plugin-level `agent_gate` with `agent_type`
`general-purpose` or `Explore` and no roster file, so "unresolved = readonly"
changes their behavior (today `agent_gate` never fires for them).

## P2 - where `CLAUDE_PROJECT_DIR` and `CLAUDE_PLUGIN_ROOT` are visible

**Verdict: visible in hooks only. Absent from every Bash tool environment. A
SessionStart `CLAUDE_ENV_FILE` export reaches Bash (main and subagent) and
`shell=True` children, but not other hooks.**

Command (`out/p2`): `python .../probe_env.py main-bash` in the main thread and
the same through a `general-purpose` subagent; the script records `os.environ`
and a `subprocess.run("set", shell=True)` child. The plugin SessionStart hook
wrote `export AGENTRY_ROOT=...` and `export PROBE_ENV=...` into
`$CLAUDE_ENV_FILE`.

| Where | `CLAUDE_PROJECT_DIR` | `CLAUDE_PLUGIN_ROOT` | `CLAUDE_ENV_FILE` | `AGENTRY_ROOT` (env-file export) |
|---|---|---|---|---|
| (a) plugin hook, SessionStart | `E:/.../proj2` | `E:/.../plugin` | `...\session-env\<id>\sessionstart-hook-N.sh` | not yet |
| (a) plugin hook, PreToolUse | set | set | absent | **absent** |
| (a) settings.json hook | set | **absent** | SessionStart only | absent |
| (b) main-thread Bash | **absent** | **absent** | absent | present |
| (c) subagent Bash | **absent** | **absent** | absent | present |
| (d) `shell=True` child of that Bash | **absent** | **absent** | absent | present |

- `CLAUDE_PLUGIN_DATA` (`C:/Users/AlexWaha/.claude/plugins/data/probe-inline`) is
  also set in plugin hooks only.
- My own subagent Bash in the live workspace session showed the same absence
  (`env | grep ^CLAUDE` lists session variables, no project or plugin root),
  although that workspace's hooks use `$CLAUDE_PROJECT_DIR` in their commands.
- Each SessionStart hook gets its own env file (`sessionstart-hook-0.sh`,
  `-1.sh`, ...); later files win when two hooks export the same name (the
  settings hook wrote `AGENTRY_ROOT=none`, the plugin hook then wrote the root).
- (e) A nested `claude -p` inherits the parent environment into its Bash tool
  and does not override it. `out/p2e`: launched with
  `CLAUDE_PROJECT_DIR=E:/bogus-parent-root AGENTRY_ROOT=bogus-parent-agentry`,
  its Bash saw `CLAUDE_PROJECT_DIR=E:/bogus-parent-root` (stale, wrong) while its
  hooks saw the correct `E:/.../proj2`. `AGENTRY_ROOT` in Bash was replaced by
  the env-file export (correct), while the hooks still saw the inherited bogus
  value.

Meaning:
- S3 `project_root()`: reading `CLAUDE_PROJECT_DIR` is correct inside hook
  entry points only. A script run from Bash (`advance.py`, `approve.py`,
  `memory.py`, gate commands, the supervisor) sees it only if `session_start`
  exports it. The plan already says to export both names; this confirms it is
  required. Export `CLAUDE_PROJECT_DIR` too, because an env-file export is the
  only thing that beat a stale inherited value in (e).
- The supervisor `spawn_cmd` must set `CLAUDE_PROJECT_DIR` explicitly in the
  child environment rather than inherit it.
- A script must not assume `CLAUDE_PLUGIN_ROOT` in a settings.json hook or in
  Bash. `plugin_root()` stays script-relative (C4).
- Only the plugin's own SessionStart hook should export `AGENTRY_ROOT`.
- Not measured: env-file behavior on `resume`, `compact` and `clear`.

## P3 - skill names, bare-name resolution, preload, `verify`

**Verdict: the `/agentry:` prefix is NOT forced. A unique plugin skill answers
to both `/name` and `/plugin:name`; a project agent preloads a plugin skill by
bare name. The `verify` pre-commit trigger is inconclusive.**

Init event of every run (`system/init`, `skills` and `slash_commands`):

```
'proj-skill', ..., 'probe:probe-skill', 'probe:probe-skill2', 'probe:verify', ..., 'verify' (bundled)
```

The plugin skills are listed namespaced. Invocation (`out/p3`, token only in the
skill body, not in the description):

```
claude -p --model haiku --setting-sources project --plugin-dir $B/plugin \
  "/probe:probe-skill What is the secret token? Reply with the token only."   -> PLUGIN-TOKEN-A-7731
claude -p ... "/probe-skill What is the secret token? Reply with the token only."  -> PLUGIN-TOKEN-A-7731
```

Preload (agent `sk-agent`, `skills: [proj-skill, probe-skill, probe:probe-skill2]`):

```
[DEBUG] [Agent: sk-agent] Preloaded skill 'proj-skill'
[DEBUG] [Agent: sk-agent] Preloaded skill 'probe-skill'
[DEBUG] [Agent: sk-agent] Preloaded skill 'probe:probe-skill2'
```

and the agent reported all three tokens. A bare name resolves to the plugin
skill, and so does the prefixed form. Not measured: a bare name that exists both
in a project and in a plugin.

`${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_SKILL_DIR}` written in a SKILL.md body are
substituted, with forward slashes, both on slash invocation and on agent preload:

```
/probe-subst now  ->  ROOT=E:/Personal/Agenty/tmp/plugin-probes/plugin
                      DIR=E:/Personal/Agenty/tmp/plugin-probes/plugin/skills/probe-subst
```

`verify` pre-commit trigger (sonnet, `--permission-mode acceptEdits`, prompt:
write a file, stage it, then commit it as a separate command; `out/t4*`). The
Skill tool call is the observable.

| Run | Skill `verify` present as | Skill call before the commit |
|---|---|---|
| T4 first run | plugin `probe:verify`, description repeats "runs right before it commits" | yes: `Skill probe:verify` |
| A, A2 | plugin `probe:verify`, plain description | no, no |
| B | project `verify`, plain description | yes: `Skill verify` |
| B2 | project `verify`, plain description | no |
| C | none (bundled `verify` only) | no |

Meaning: the trigger fires nondeterministically in headless mode, n=1 or 2 per
cell, so no claim of equivalence between the two locations is supportable. It
fired for a plugin skill when the description asked for it, and it fired for
the project skill once without that help. D4's default (keep `skills/verify/`
project-side) is the safe reading and costs nothing. Plan risk 11 (preload of 18
agents by bare name) is closed: no agent file needs an `agentry:` entry. D11
(accept the `/agentry:` prefix) becomes moot for slash use. Model-facing
names in the Skill tool are still prefixed (`probe:verify`).

## P4 - Windows plugin root in a hook command

**Verdict: in shell-form hook commands `${CLAUDE_PLUGIN_ROOT}` is rendered with
forward slashes and works quoted or unquoted unless the path has a space; always
quote it, or use exec form.**

Commands (`out/p4a`, `out/p4b`): `--plugin-dir 'E:\Personal\Agenty\tmp\plugin-probes\plugin'`
and `--plugin-dir "$B/plugin with space"`, one trivial prompt, four SessionStart
entries in `hooks.json`:

```
"python \"${CLAUDE_PLUGIN_ROOT}/scripts/dump.py\" plugin-sessionstart-quoted --envfile"
"python ${CLAUDE_PLUGIN_ROOT}/scripts/dump.py plugin-sessionstart-unquoted"
{"type":"command","command":"python","args":["${CLAUDE_PLUGIN_ROOT}/scripts/dump.py","plugin-sessionstart-exec"]}
```

| Plugin dir given as | quoted | unquoted | exec form | `CLAUDE_PLUGIN_ROOT` seen by the script |
|---|---|---|---|---|
| `E:\...\plugin` (backslashes) | ran | ran | ran | `E:/.../plugin` (shell form), `E:\...\plugin` (exec form); `sys.argv[0]` `E:\...\plugin/scripts/dump.py` in exec form |
| `E:/.../plugin with space` | ran | **did not run** | ran | `E:/.../plugin with space` |

`claude plugin validate $B/plugin` flags the unquoted entry: `hooks.SessionStart:
Shell command uses ${CLAUDE_PLUGIN_ROOT} without quotes ... Wrap the placeholder
in double quotes, or use exec form`. Exit 0 with warnings; `--strict` exits 1.

Meaning for S5: every one of the 45 `hooks.json` commands wraps the placeholder
in double quotes (a user profile with a space is common). Run `claude plugin
validate plugin/` as part of the S5 evidence; it needs to report no hook warning.
Python code that reads `CLAUDE_PLUGIN_ROOT` must go through `pathlib.Path`, never
string compare: the two spellings above differ. Add `author` to `plugin.json` to
silence the one manifest warning (optional).

## P5 - can a plugin carry `statusLine`, `permissions`, `env`

**Verdict: no for `env`, `permissions`, `hooks`, `claudeMdExcludes` in a plugin
`settings.json` (measured). `statusLine`: the manifest validator says it is
ignored; the render itself is not observable headless.**

Commands: `$B/plugin/settings.json` with `env`, `permissions.deny`, `statusLine`,
a `Stop` hook and `claudeMdExcludes`; then (`out/p5`, `out/p5b`) a Bash
`echo plugin-denied-test` against the deny rule and a Python one-liner reading
`PROBE_PLUGIN_ENV`:

```
Both commands ran. Neither was denied.            (deny rule from plugin settings.json not applied)
PLUGIN_ENV=None                                    (env from plugin settings.json not applied)
no plugin-settings-stop-hook-*.json written        (hooks from plugin settings.json not applied)
```

Manifest check, `claude plugin validate --strict` on a `plugin.json` that carries
the fields:

```
  > statusLine: Unknown field 'statusLine'. Claude Code ignores it at load time.
  > env: Unknown field 'env'. Claude Code ignores it at load time.
  > permissions: Unknown field 'permissions'. Claude Code ignores it at load time.
x Validation failed (--strict treats warnings as errors)      exit 1
```

Meaning: the plugin cannot ship `statusLine` or any project setting; `claudeMdExcludes`,
`env` and `permissions` stay in the project `settings.json` (consistent with C2).
D5 applies: `statusLine` leaves the template settings and becomes a snippet in the
0042 README. `statusLine` could not be observed rendering because `-p` never draws
it; the evidence is the validator message plus the three settings keys above that
the same file failed to apply.

## Extra: plugin hooks deliver context the way the harness needs

Not in the original list, measured because S5 depends on it (`out/p6`): a plugin
`SubagentStart` hook printing `{"hookSpecificOutput": {"hookEventName":
"SubagentStart", "additionalContext": "INJECT-SUB-TOKEN-5521 ..."}}` reached a
`general-purpose` subagent, and a plugin `SessionStart` hook's plain stdout
(`INJECT-MAIN-TOKEN-6632`) reached the main thread. Rule chunking and memory
injection survive the move to `hooks.json`.

## What the results change in the plan

1. Nothing in P0: agents stay project-side. D6 not triggered.
2. S3: export `CLAUDE_PROJECT_DIR` (as well as `AGENTRY_ROOT`) from the plugin
   `session_start` to `$CLAUDE_ENV_FILE`; give `spawn_cmd` an explicit child
   environment. Without the export no Bash-launched script has a project root.
3. S4: the `profile:` key is safe. Plugin-level `agent_gate` also fires where
   frontmatter hooks would be skipped (untrusted folder), a net gain.
4. S5: quote `${CLAUDE_PLUGIN_ROOT}` in all 45 commands; add `claude plugin
   validate plugin/` to the evidence. Plugin `SubagentStart`/`SessionStart`
   injection works.
5. S6: skills may use `${CLAUDE_PLUGIN_ROOT}/tools/...` in SKILL.md bodies
   (substituted on invocation and preload), which needs no env file; keep
   `$AGENTRY_ROOT` only in prose that is not a skill (rules, both CLAUDE.md,
   agents). The prefix is optional, not required.
6. Risk 11 closed; risk 10 (`verify`) stays at the D4 default.

## Not settled here (manual steps)

| Item | Why it did not run | Manual step |
|---|---|---|
| Marketplace install path (`claude plugin marketplace add`, `install`, `enabledPlugins`) | writes `~/.claude`, which this task must not touch | task-0039: install from a local marketplace on a scratch `CLAUDE_CONFIG_DIR` or the User's own go-ahead; compare `CLAUDE_PLUGIN_ROOT` (expected a copy under `plugins/cache`) with the source tree, since any file the plugin reads outside its own root breaks in a cached copy |
| `statusLine` rendering | `-p` draws no status line | interactive session with `--plugin-dir` and a plugin `settings.json` carrying `statusLine`; look at the status bar |
| `verify` pre-commit trigger | nondeterministic in `-p` | interactive session, plugin `verify` with a neutral description, several commits, count Skill calls |
| Real project agent as the P0 control | needs the folder trusted interactively | open `$B/proj` once in an interactive `claude`, accept the trust dialog, rerun `out/p0-d` with `pagent-proj` |
| Env-file on `resume`/`compact`/`clear`; bare-name collision of plugin and project skill | not asked, not run | one run each when 0039 installs the plugin |

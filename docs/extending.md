# Extending Agentry

You add an agent, a rule or a skill by writing a file. The hooks and the tests read those files, so the file is the whole extension surface. This page shows the file for each, the line that registers it, and what it costs. The last section covers running Agentry beside other Claude Code plugins.

Claims on this page trace to a file, a key or a test in the repository. Where the repository has no measurement, the text says "not measured".

## Table of contents

- [Where each step applies](#where-each-step-applies)
- [Add your own agent](#add-your-own-agent)
- [Add a rule](#add-a-rule)
- [Add a skill or command](#add-a-skill-or-command)
- [Run Agentry beside other plugins](#run-agentry-beside-other-plugins)

## Where each step applies

Agents, rules, `settings.json` and project skills live in your project's `.claude/`, so you can edit them. Hooks, `hooks.json` and the test suite live in the plugin (`plugin/` in the repository). See [Configuration](configuration.md#where-things-live) for the split.

| Step | Installed plugin | Fork of the repository |
|---|---|---|
| Agent file, roster row | Yes | Yes |
| Rule file, `claudeMdExcludes`, `rules:` key | Yes | Yes |
| Project skill | Yes | Yes |
| Edit `hooks.json` (chunk wiring, memory and handoff matchers) | No, the plugin payload is replaced on upgrade (an update is a real copy, `docs/technical/plugin-install-2.1.299.md`, lines 150-151) | Yes |
| Edit the tests in `plugin/tools/tests` | No | Yes |

## Add your own agent

### The file

An agent is one Markdown file at `.claude/agents/<name>.md`: frontmatter between two `---` lines, then the prompt. The name is the file stem. The hooks look the agent up by that stem, so use lowercase letters, digits and hyphens only, starting with a letter, at most 64 characters. The hook rejects dots (`AGENT_NAME_RE` in `inject_rules.py`), and the roster check in `test_agent_frontmatter.py` accepts only `[a-z][a-z0-9-]+`.

This is a complete, valid agent. It drafts release notes and may write only under the folders the `docs` profile allows.

```markdown
---
name: release-notes
description: Drafts release notes from the task files merged since the last tag. Use after a release is tagged or when asked for release notes.
model: sonnet
color: green
permissionMode: bypassPermissions
effort: medium
maxTurns: 30
tools: Read, Write, Edit, Glob, Grep
skills:
  - update-docs
profile: docs
---

# Release notes writer

Read the files in `.agentry/tasks/done/` merged since the last tag. Write one
entry per task in `docs/release/`, in plain English, newest first.
```

Add one row to the roster in `.claude/CLAUDE.md`, under the department the agent belongs to. This one goes in the Support table:

```markdown
| release-notes | docs | writing release notes from merged tasks |
```

Start a new session. Agent definitions load at session start, so a running session keeps the old set (`docs/technical/claude-code-2.1.296-delta.md`, section 5.4).

### Frontmatter keys that matter

| Key | What it does | Rule |
|---|---|---|
| `name`, `description` | Identity and the text the orchestrator reads to decide when to dispatch the agent | `description` should say when to use the agent |
| `model` | Model alias | The tests accept `sonnet`, and `opus` only for `architect` |
| `color` | Label color in the Claude Code interface | One color per department, four departments, all distinct |
| `permissionMode: bypassPermissions` | Stops permission dialogs from reaching you through the main session | Required on every agent. The profile gate, not a dialog, restrains the agent |
| `effort` | Reasoning effort | One of `low`, `medium`, `high`, `xhigh`, `max` |
| `maxTurns` | Turn limit | Tied to the profile: 150 for `dev`, 100 for `readonly`, 30 for `docs` |
| `tools` | Tools the agent may call | Comma-separated list |
| `profile` | Gate profile: `dev`, `docs` or `readonly` | Read by the plugin's gate from this file. See [Gate profiles](agents.md#gate-profiles) |
| `rules` | Rule files delivered to this agent at dispatch | Paths relative to `.claude/rules/`. See [Add a rule](#add-a-rule) |
| `skills` | Skills preloaded into the agent | See [Preloading a skill into an agent](#preloading-a-skill-into-an-agent) |
| `omitClaudeMd: true` | Runs the agent without the user, project and local `CLAUDE.md` files. Managed policy files and nested `CLAUDE.md` files still load (`claude-code-2.1.296-delta.md`, section 5.1) | Only for `docs` and `readonly` agents. The agent must then list the six core rules first in `rules:` |
| `allow_scripts: [a, b]` | Programs a `readonly` or `docs` agent may run in the shell, which is otherwise default-deny | A one-line list only. A block-style list grants nothing. Never combine with `omitClaudeMd`. A name grants the whole program: `npx` installs and runs any package, and an interpreter in the list reopens the shell (`.claude/rules/orchestration.md`, "Subagent enforcement") |

Do not write a `hooks:` block. The plugin registers the gate once for every agent (`plugin/hooks/hooks.json`, PreToolUse) and reads the `profile:` key from the file. A `hooks:` block would be ignored on a plugin agent and would run the gate twice on a project agent. The test `test_every_agent_declares_a_profile_and_no_hooks` fails on any agent that has one.

A `rules:` entry is a path relative to `.claude/rules/`. A short example:

```yaml
rules:
  - docs-style.md
  - documentation.md
```

Three more behaviors to know before you rely on a new agent:

- A wrong `profile:` value does not fail. The lookup returns nothing for a value outside `dev`, `docs` and `readonly`, so the agent silently resolves to `readonly` (`agent_profile.py`, lines 83-84).
- Memory and handoff injection are matched by agent name. `hooks.json` lists 12 agents in its SubagentStart matchers (lines 164, 184 and 334), so a new agent gets neither unless you add its name there. That is a plugin edit, so it applies to a fork only.
- A subagent cannot write `.claude/agents`, `.claude/rules` or `.claude/skills` (`pretool_gate.py`, `LIVE_HARNESS_RE`). Create these files yourself or from the main session, not by dispatching an agent.

Sources: `plugin/tools/pipeline/agent_profile.py`, `plugin/tools/tests/test_agent_frontmatter.py`, `plugin/tools/setup/apply_optimization.py` (`MAX_TURNS`).

### Register it in the roster

The roster tables in `.claude/CLAUDE.md` are machine-parsed. Each row has the form `| name | dev|readonly|docs | when to dispatch |`, under one of four headings: Technical, Support, Business, Content & Growth. The profile in the row must equal the `profile:` key in the file (`test_each_agent_profile_matches_the_roster_table`).

### What the tests pin when you fork

If you work in a clone of the repository, `plugin/tools/tests/test_agent_frontmatter.py` checks all agents against fixed expectations. A thirty-second agent turns it red until you edit the constants it names. Edit the constants, not the assertions.

The agent count of 31 is also written into three other test modules. Adding the example agent to a clone turned all four red (measured), so raise the number in each:

| Module | Where the 31 sits |
|---|---|
| `test_agent_frontmatter.py` | `AGENT_COUNT = 31` |
| `test_inject_rules.py` | `AGENT_COUNT = 31`, read by `test_every_shipped_declaration_resolves_once` |
| `test_agent_profile.py` | the literal `31` in `test_every_shipped_agent_resolves_to_a_profile_and_the_roster_agrees` |
| `test_payload.py` | the literal `31` in `test_every_agent_resolves_to_a_known_profile` |

The rows below are the checks inside `test_agent_frontmatter.py`.

| Test | What goes red | Edit |
|---|---|---|
| `test_agent_count_is_31`, `test_every_field_present_on_every_agent`, `test_each_agent_profile_matches_the_roster_table` | `AGENT_COUNT = 31` | Raise `AGENT_COUNT` |
| `test_departments_cover_every_agent_exactly_once` | Agent missing from, or twice in, the roster | Fix the roster row |
| `test_color_is_one_per_department_and_four_distinct` | Color differs from its department | Copy the color of a sibling agent |
| `test_max_turns_matches_the_gate_profile` | `maxTurns` not 150, 100 or 30 | Set it to the value for the profile |
| `test_every_agent_declares_a_profile_and_no_hooks` | Missing or invalid `profile:`, or a `hooks:` block | Fix the file |
| `test_omit_claude_md_is_on_exactly_the_named_agents` | `omitClaudeMd` on an agent not in `OMIT_CLAUDE_MD_AGENTS` | Add the agent to that set |
| `test_allow_script_flags_are_on_exactly_the_three_named_readonly_agents` | `allow_scripts` on a fourth agent | Add it to `ALLOW_SCRIPT_AGENTS` |
| `test_cache_ttl_is_on_exactly_the_eight_named_agents` | `cacheTtl` on a ninth agent | Add it to `CACHE_TTL_AGENTS` |
| `test_effort_matches_the_plan` | Runs only where the gitignored Phase 5 plan exists, and then needs a row for the new agent | Add the row, or leave the plan absent |

The example agent above avoids the three rows about `omitClaudeMd`, `allow_scripts` and `cacheTtl`: it sets none of them. Measured on a clone without the plan file: every other row passed once the count was raised, and the effort row was skipped.

Run the four modules from the repository root, narrowed to what you changed:

```bash
python -m unittest discover -s plugin/tools -p "test_agent_frontmatter.py"
python -m unittest discover -s plugin/tools -p "test_agent_profile.py"
python -m unittest discover -s plugin/tools -p "test_inject_rules.py"
python -m unittest discover -s plugin/tools -p "test_payload.py"
```

When a `dev` agent runs these, its gate denies a `discover` run that has no filter flag (`-p` does not count as one). Add `-k test` to each command, which matches every test in the module.

Running the suite from the installed plugin against your project's agents is not measured.

## Add a rule

A rule is a Markdown file in `.claude/rules/`. Who receives it decides the cost: every recipient pays for its size in every dispatch.

### Core rules and targeted rules

The core set is the six `@rules/` lines in `.claude/CLAUDE.md`: `quality-standard`, `communication`, `task-creation`, `code-retrieval`, `self-learning` and `pipeline`. Claude Code loads these for the main session and for every agent.

Every other file in `.claude/rules/` is named in `claudeMdExcludes` in `.claude/settings.json` (14 entries today). That key stops Claude Code from loading the file everywhere. The file then reaches only the agents whose `rules:` key lists it. At dispatch, the `inject_rules.py` hook reads that key and delivers each listed file. A listed file that does not exist under `.claude/rules/` produces a diagnostic line and never blocks the dispatch.

To add a targeted rule, three edits:

1. Write `.claude/rules/docs-style.md`.
2. Add `"**/.claude/rules/docs-style.md"` to `claudeMdExcludes` in `.claude/settings.json`.
3. List `docs-style.md` under `rules:` in the frontmatter of each agent that should follow it.

Skip step 2 and the rule loads for everyone anyway. `test_claude_md_excludes_is_the_complement_of_the_core_set` fails on that. `test_rules_dir_is_covered_n_of_n` fails if a rule file is reached by no channel at all, so a rule nobody lists is an error.

To add a core rule, put an `@rules/<file>.md` line in `.claude/CLAUDE.md` and leave the file out of `claudeMdExcludes`. Every agent that sets `omitClaudeMd: true` must then also list the new file in `rules:`, directly after the existing six, or `test_an_agent_that_omits_claude_md_declares_the_whole_core_set` fails. That test also pins the number six, so adding a seventh core rule means editing it too.

Two more fork traps for a core rule (static reading of the tests, not a measured run): add a row with a reason to the `## Rules` table in `.claude/CLAUDE.md`, or `test_every_core_rule_has_a_recorded_reason` fails (`test_inject_rules.py`, lines 703-714). Put the new `@rules/` line last in the list.

### Rules only the orchestrator gets

`git-workflow.md` and `orchestration.md` are delivered to the main session only. They are in `RULES` in `plugin/tools/hooks/main_thread_rules.py`, and the hook prints them on SessionStart, split into numbered `--chunk N` entries plus a final `--index` entry in `hooks.json`. They are also in `claudeMdExcludes`, and no agent may list them (`test_no_agent_declares_a_main_thread_rule`). You do not add to this group in an installed plugin, because `RULES` is plugin code.

### What each choice costs

| Choice | Cost |
|---|---|
| Core rule | Its size, in every dispatch. An agent with `omitClaudeMd: true` pays it too, because it receives the core rules through `rules:` |
| Targeted rule | Its size, in dispatches of the agents that list it |
| Main-thread rule | Its size, once per session and again after each `/compact`, plus one more `hooks.json` chunk when it grows past the budget |

Measure a rule before you add it: `wc -c .claude/rules/<file>.md` gives its size in bytes. For scale, the project and user `CLAUDE.md` files plus the six core rules came to 87,745 characters in a plain subagent on Claude Code 2.1.296. With `omitClaudeMd: true` the agent skips only the `CLAUDE.md` files (36,489 characters) and still gets the six core rules through `rules:`. That saved about 14.1k tokens per dispatch (`docs/technical/claude-code-2.1.296-delta.md`, section 5.2, measured with headless `claude -p`).

One ceiling applies. Claude Code replaces any hook output over 10,000 characters with a 2,000-character preview. `inject_rules.py` stays under it by splitting a rule block into chunks, and `hooks.json` wires 13 `--chunk N` entries for it today. The agent with the largest `rules:` list sets how many are needed. A rule that pushes an agent past 13 chunks needs a new `hooks.json` entry, and `ChunkBudgetTest` fails until it exists. That is a plugin edit, so in an installed project the limit is fixed at 13. Read the current count from `plugin/hooks/hooks.json`.

## Add a skill or command

### Project skills and plugin skills

A command is a skill. Both live at `<skills directory>/<name>/SKILL.md`, with frontmatter and a body:

```markdown
---
name: changelog-draft
description: Draft a Keep a Changelog entry from the task files merged since the last tag. Use when asked for a changelog draft.
allowed-tools: Read, Glob, Grep
---

# Changelog draft

1. Read the files in `.agentry/tasks/done/` merged since the last tag.
2. Group them under Added, Changed and Fixed.
3. Print the entry. Do not edit CHANGELOG.md.
```

Put your own skills in your project's `.claude/skills/`, for example `.claude/skills/changelog-draft/SKILL.md`. The plugin ships 42 skills in `plugin/skills/`, and the plugin payload is replaced when you upgrade (`docs/technical/plugin-install-2.1.299.md`, lines 150-151), so a file you add there does not survive. Start a new session and type `/changelog-draft`.

In a fork, a new skill directory turns `test_readme_counts_match_the_directories` in `plugin/tools/tests/test_verify_skill.py` red (static reading, lines 85-92). The directory count goes from 43 to 44, and `plugin/skills/README.md` line 10 says "listing shows 43". Update the README counts to match.

Agentry keeps one skill in the project on purpose: `verify`. Claude Code calls a skill named `verify` before it commits. In probe P3, with `verify` as a plugin skill, that call fired nondeterministically in headless mode, so the default is to keep it at `.claude/skills/verify`, where it delegates to the plugin's `health-check` (`docs/technical/plugin-layout.md`, "The verify skill").

### Names

Claude Code lists plugin skills with the plugin prefix, as `agentry:<skill>`. A plugin skill with a unique name also answers to the bare `/name` (probe P3 in `docs/technical/plugin-layout.md`). A project skill and a plugin skill with the same bare name is not measured, so pick a name that is not in [Commands](commands.md).

### Preloading a skill into an agent

List the skill by bare name under `skills:` in the agent's frontmatter. `architect` lists `api-design` and `db-design` this way, and the example agent above lists `update-docs`. A project agent preloading a plugin skill by bare name is measured (probe P3). Preloading a project skill the same way is not measured in this repository.

## Run Agentry beside other plugins

Not measured. This repository has no test or recorded run with another plugin's hooks, output style or agents active next to Agentry. The list below is the full set of gaps, so you know what to check yourself.

- How two plugins' hooks for the same event order and combine, and whether one can veto the other's output.
- The token cost of another plugin's SessionStart or SubagentStart text on top of Agentry's.
- Whether another plugin's hook that approves or rewrites a tool call changes the outcome of an Agentry deny.
- A skill in another plugin with the same name as an Agentry skill.
- Which output mechanism plugins such as Caveman or Ponytail use. This page makes no claim about their internals.
- Whether the 10,000-character limit on hook output applies to another plugin's hooks. The limit is measured for Agentry's hooks on 2.1.296, and a large block from another plugin should hit the same code path.

What follows is what the code does, whatever else is installed.

### What composes

Agentry registers its hooks in its own plugin, `plugin/hooks/hooks.json`, and its agents and rules in your project. Whether two plugins' hooks run together is Claude Code's behavior and is not measured here (see the list above).

An output-style setting changes the main conversation only. `outputStyle` does not reach subagents, which is why Agentry sets the report format for agents in `communication.md` instead (`.claude/rules/communication.md`, "Asking the User").

### What Agentry's gates still apply to

| Gate | Applies to | Source |
|---|---|---|
| `pretool_gate.py` | Every Bash, Edit, Write, MultiEdit and NotebookEdit call, in the main session and in every subagent, whichever plugin the subagent came from | `hooks.json`, PreToolUse matcher `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit` |
| `agent_gate.py` | The same tools plus WebSearch and WebFetch, for every subagent | `hooks.json`, matcher `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit\|WebSearch\|WebFetch` |

`pretool_gate.py` is what denies a commit or push without a recorded approval (see [Troubleshooting](troubleshooting.md#hook-denies-in-plain-words)). `agent_gate.py` finds the caller from the payload's `agent_type`, reads `<project>/.claude/agents/<agent_type>.md` and applies its `profile:`.

### What they do not cover

An agent that ships in another plugin arrives as `plugin:name`. The colon is not a legal character in a file stem, so the lookup never reaches the filesystem and the agent resolves to `readonly` (`agent_profile.py`: `NAME_RE`, `FALLBACK_PROFILE`; tests `test_resolve_falls_back_to_readonly_for_every_unresolved_caller` and `test_an_agent_with_no_definition_is_readonly`). The same holds for the built-in `general-purpose` and `Explore` agents. A plugin may only tighten what a project allows, so an unknown caller fails closed.

A foreign agent is therefore held to read-only:

- It cannot write or edit files. The hook denies with a message starting `This agent is read-only`.
- Shell commands are default-deny except a short list of read-only programs, so a `git push` from it is refused.

If you want a foreign agent to write, Agentry will not allow it as it arrives. A copy of its definition in `.claude/agents/<name>.md` with a `profile:` line is a different agent: a project agent that resolves by file stem, the same lookup that serves your own agents. The original `plugin:name` stays `readonly`. A copied plugin agent has not been tested.

Two limits apply to the `dev` profile, which is what a copied agent gets if you give it `profile: dev`. A `dev` agent runs code, so an interpreter that writes a file (`python -c "open(...)"`) is not read by the gate, and the profile fails open when its gate crashes. `readonly` and `docs` agents have a default-deny shell, so neither limit applies to them. Both are listed under "Subagent enforcement" in `.claude/rules/orchestration.md`.

### Check for conflicts

Run these after you add another plugin. Each one reads something Agentry already prints.

1. Run `claude plugin list` and confirm both plugins show as enabled.
2. Start a session. The SessionStart status messages from Agentry appear, such as "Session start: pipeline state, codegraph sync, memory health...". The main-thread rules finish with a line `[main-thread-rules] delivered K of K chunks`, printed by `main_thread_rules.py`. SessionStart output repeats after `/compact`, so you see it again then.
3. Dispatch `technical-writer`, which declares rules in `rules:`, and ask it to quote the first line of its context that begins `[agent-rules]` and every line that begins `[agent-rules part i of K]`. The `delivered K of K chunks` line proves the hook ran. It prints K twice by construction, even if a chunk is not wired in `hooks.json` (`inject_rules.py`, lines 279-283), so matching numbers prove nothing about completeness. The check is that every part from 1 to K appears. An agent with no `rules:` key prints nothing at all (`inject_rules.py`, lines 280-281), so do not use one for this check.
4. Check the deny text with two probes. Ask a `general-purpose` agent to write a file with the Write tool. Expect a deny that starts `This agent is read-only` (`agent_gate.py`, `handle_readonly`; measured in `docs/technical/plugin-install-2.1.299.md`, lines 311 and 419). `reviewer` has no Write tool (`.claude/agents/reviewer.md`, line 11), so ask it to write a file through Bash instead and expect `Read-only agent: mutating Bash denied`. Both strings are listed in [Troubleshooting](troubleshooting.md#hook-denies-in-plain-words). If a write succeeds, a hook is not firing.
5. If an expected block of context is missing, compare its size to 10,000 characters. Claude Code swaps any hook output over that for a 2,000-character preview. That applies to another plugin's SessionStart text as well, by the same code path, though only Agentry's hooks were tested.

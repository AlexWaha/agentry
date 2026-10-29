# Getting started

## Table of contents

- [Requirements](#requirements)
- [Install the plugin](#install-the-plugin)
- [Onboard your project](#onboard-your-project)
- [Your first task](#your-first-task)
- [What to read next](#what-to-read-next)

## Requirements

| Need | Why |
|---|---|
| Claude Code | Agentry is a Claude Code plugin and relies on its hooks and subagent dispatch |
| Python 3.12 or newer on PATH | The pipeline tools are Python. They use only the standard library, so there is no `pip install` step |
| git | Branch checks, merge detection and the commit checkpoint |
| ruff (optional) | Only used by this repository's own `review` gate. Your gate can be any lint command |
| codegraph (optional) | A code index for faster structural queries. Without it, agents fall back to Grep and Glob |

## Install the plugin

Run these from your project root. `--scope local` limits the install to this project. Without it, Claude Code declares the marketplace at user scope and writes it into `~/.claude/settings.json`.

```bash
claude plugin marketplace add AlexWaha/agentry --scope local
claude plugin install agentry@agentry --scope local
claude plugin list
```

`claude plugin list` should show `agentry@agentry`, scope `local`, status `enabled`. A local install belongs to one project, so the same plugin appears once per project where you installed it. Two projects give two `agentry@agentry` rows. Restart Claude Code, because `hooks.json` is read at session start. On start, the plugin exports `AGENTRY_ROOT` (the cached plugin copy) and `CLAUDE_PROJECT_DIR` into every Bash call.

Claude Code lists plugin skills with the plugin name as a prefix, for example `/agentry:pipeline-show`. The prefix is optional when the skill name is unique, so `/pipeline-show` and `/agentry:pipeline-show` both work. This guide uses the short form. The dashboard works the same way: `/dashboard` and `/agentry:dashboard` both start it. Use the prefixed form if another plugin has a skill with the same name. Type slash commands inside the Claude Code session. If you pass one as a shell argument in Git Bash on Windows, the shell rewrites a leading `/pipeline-show` into a path such as `C:/Program Files/Git/pipeline-show`.

The clone is anonymous HTTPS, so the install needs no login. If you have no SSH key for GitHub, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` before the first command.

Status: measured on Claude Code 2.1.312 with HTTPS forced. To install a specific release instead of the latest `main`, see [Upgrade, pin and roll back](upgrade.md).

## Onboard your project

The plugin carries the tools, skills and hooks. The agents, rules and pipeline config come from this repository and are tailored to your stack once.

1. Clone this repository somewhere outside your project, then copy `.claude/`, `.agentry/` and `CLAUDE.md` into your project root as shown below.
2. Open Claude Code at your project root and accept the workspace trust dialog. Until you accept it, Claude Code ignores the `permissions.allow` entries from the copied `.claude/settings.json` and prints `Ignoring 22 permissions.allow entries from .claude/settings.json: this workspace has not been trusted`.
3. Paste the prompt from `.claude/_init-prompt.md`. It runs in two phases. Phase A detects your language, framework, test, format and build commands, database and CI, and fills in the placeholders. Phase B is an interview that sets the stages, their exit commands, the retry budget and which checkpoints you want to approve yourself.
4. Follow `.claude/_onboarding.md` for what the prompt did not cover: optional rules, permissions in `settings.json`, and the codegraph check.

> **Merge into `.claude/`, never replace it.** The install already created `.claude/` in your project. It holds `settings.local.json`, which carries the plugin enablement. Delete or replace the folder and the plugin is no longer enabled for this project. The copy below also overwrites files of the same name: an existing `.claude/settings.json`, any agent or rule file, and `.agentry/pipeline.json`. Back those up first. `CLAUDE.md` is copied only when you have none.

Run these from your project root in a bash shell (Git Bash on Windows, which Claude Code uses). `CLONE` is the folder where you cloned this repository. First, check the four files most likely to exist already. No output means none of them do. Agents and rule files of the same name can still be overwritten, as the box above says.

```bash
CLONE=/path/to/agentry
for f in .claude/settings.json .agentry/pipeline.json CLAUDE.md .mcp.json; do [ -e "$f" ] && echo "exists: $f"; done
```

Then copy. The trailing `/.` includes dotfiles such as `.claude/.gitignore`:

```bash
mkdir -p .claude .agentry
cp -R "$CLONE/.claude/." .claude/
cp -R "$CLONE/.agentry/." .agentry/
[ -e CLAUDE.md ] || cp "$CLONE/CLAUDE.md" CLAUDE.md
```

If a `CLAUDE.md` already existed, the commands left it alone, and you can leave it that way. The orchestrator document is already in `.claude/CLAUDE.md`, which the copy brought in. The root template only adds placeholder blocks for your project facts: product, stack, commands, environment variables and routes. If you want them, paste those blocks into your own `CLAUDE.md` and fill them in.

Neither copy removes anything, and the clone has no `settings.local.json`, so yours stays. Confirm it still enables the plugin.

```bash
grep agentry@agentry .claude/settings.local.json
```

`.mcp.json` is optional. It wires the codegraph MCP server (`codegraph serve --mcp`), so copy it only if you installed codegraph. Without it, agents fall back to Grep and Glob. If your project already has a `.mcp.json`, add the `codegraph` entry by hand instead of copying.

```bash
[ -e .mcp.json ] || cp "$CLONE/.mcp.json" .mcp.json
```

> **Check four keys before you trust the gates.** This repository ships with its own values in `pipeline.json`, so a search for leftover `{{...}}` tokens finds nothing. Four keys still need yours: `main_branch` and the exit commands of `implement`, `test` and `review`. If you keep the shipped ones, your `implement` gate compiles this harness instead of building your code. It passes and proves nothing. "Decision 2: gates per repository" in `.claude/_onboarding.md` lists each key with its shipped value.

After step 4, `/pipeline-show` prints three lines, one per dial (mode, approvals and swarm), as soon as the plugin works. It prints them on a project that was only copied and never onboarded, so it does not prove onboarding ran. To check that, search for the shipped gate commands.

```bash
grep -n "dashboard_gate\|plugin/tools" .agentry/pipeline.json
```

Onboarding ran when this prints nothing. Any match means a gate still runs this repository's commands. Then confirm the four keys from the box above, and that `.agentry/project/stack.md` describes your stack.

## Your first task

This walk-through uses the default build flow. Words in `code` are stages or commands you can look up in the [pipeline page](pipeline.md) and the [command reference](commands.md).

**1. Pick your dials.** Type `/pipeline-build-manual`. That sets mode to build, keeps every checkpoint with you, and keeps swarm off. Then `/pipeline-show` confirms it. Avoid `/pipeline-build-assisted` for this walk-through: assisted skips the commit prompt, so step 6 would never happen. The `auto` level also skips the take step and the commit prompt. A push is never automatic at any level.

**2. File a task.** Ask Claude: "Use the new-task skill to file a task: add a /health endpoint that returns 200." It writes a file into `.agentry/tasks/backlog/`.

**3. Start it.** Tell Claude to start the task. The orchestrator works on a branch named `<type>/<task>`, which must be cut from up-to-date `main`, registers the task, and moves its file to `active/`.

**4. Approve the plan.** The task's owner agent writes a plan, and the pipeline parks at `plan-approval`. Claude shows you the plan and asks. Nothing outside `.agentry/` can be edited until you say yes. If you say no, the task goes back to `plan` and you have lost nothing but a document.

**5. Let it run.** `implement`, `test` and `review` run on their own. At each stage end, a script runs your gate command and records the result. If a gate fails, the owning agent fixes it and the script runs it again. After three failures the task is parked as blocked and Claude tells you why.

**6. Read the diff.** At `ready`, type `/diff` in the session to read what changed. Approve the commit, or reject it with a reason and the task returns to `implement`.

**7. Merge.** What happens next depends on the workflow mode in `pipeline.json`:

| Workflow mode | After the commit approval |
|---|---|
| `solo` | The pipeline merges the task branch into `main` locally. There is no push step |
| `pr` | You push the branch with a second, separate approval, open a pull request and merge it yourself |

The task reaches `done` only when git confirms the branch is merged. The file moves to `done/`.

**8. Let the handoff run.** Before the next task can start, the previous one needs a short handoff note and a memory review. Claude writes both, so you only need to know why the next task starts a little late.

**9. Open the dashboard (optional).** Type `/agentry:dashboard`. Claude starts the read-only server in the background, opens your browser and gives you one URL of the form `http://127.0.0.1:<port>/#token=<token>`. The token belongs to this launch, so treat the URL like a password. The page shows the task you just ran. Type `/agentry:dashboard stop` to end it. See [Dashboard](dashboard.md).

## What to read next

- [Pipeline and approvals](pipeline.md) to understand each stage and every checkpoint.
- [Commands](commands.md) for the full list.
- [Troubleshooting](troubleshooting.md) when a hook says no.

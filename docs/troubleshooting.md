# Troubleshooting

## Table of contents

- [Install and startup](#install-and-startup)
- [Hook denies in plain words](#hook-denies-in-plain-words)
- [Task and pipeline behavior](#task-and-pipeline-behavior)
- [Windows notes](#windows-notes)

## Install and startup

| Symptom | Cause | Fix |
|---|---|---|
| `marketplace add` fails to clone | The clone tried SSH and you have no GitHub key | Set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` and run it again |
| Hooks do nothing after install | `hooks.json` is read at session start | Restart Claude Code |
| `AGENTRY_ROOT` is empty in a Bash call | The session start hook has not run | Restart. Check `claude plugin list` shows `enabled` for this project |
| `claude plugin list` shows the plugin as `disabled` | The install is per project and you are in another directory | Run it from the project root where you installed |
| Every hook fires twice | Two enabled copies of the plugin | Uninstall the one you do not need |
| The marketplace landed in `~/.claude/settings.json` | `--scope local` was left off | Remove the marketplace (it cascades, see [Roll back](upgrade.md#roll-back)) and add it again with `--scope local` |
| `Remote branch <sha> not found` while pinning | A commit SHA cannot be a pin | Use a tag or a branch name after `#` |
| `refusing {plugin}` (or `refusing {root}`) in a gate result | The plugin path holds a space or a non-ASCII character, for example a Windows user name with a space. Gate commands splice the path in unquoted, so only `A-Z a-z 0-9 _ . / : -` are allowed | Install where the path is plain ASCII without spaces, or point `CLAUDE_CODE_PLUGIN_CACHE_DIR` at such a folder and install again |
| Gates pass but prove nothing | `pipeline.json` still has this repository's gate commands | Set `main_branch` and the exit commands of `implement`, `test` and `review`. See [Configuration](configuration.md#stage-exit-gates) |

## Hook denies in plain words

A deny is not a bug. A hook checked a rule and said no. The message names the rule. These are the ones you will meet most.

| Message starts with | What it means | What to do |
|---|---|---|
| `Not on a feature branch (no task-XXXX)` | You tried to commit on `main`, or on a branch not named for a task | Create the task branch from up-to-date `main`, as the message says |
| `Commit for <task> is not approved yet` | The commit checkpoint has no recorded approval | Read the diff and approve. Claude records it, then the commit goes through |
| `Push for <task> is not approved yet` | The same, for a push | Approve the push in chat. No approval level grants it on its own |
| `Orchestrator never writes code` | The main session tried to edit a file outside its allowed folders | Dispatch the owning agent. This is the intended path, not a failure |
| `This agent is read-only` | A readonly agent, or one the hooks could not identify, tried to write | Dispatch an agent whose profile allows the change. See [Agents](agents.md#gate-profiles) |
| `Read-only agent: mutating Bash denied` | A readonly agent ran a shell command that changes files | Same as above |
| `Handoff debt: completed task ... has no valid handoff doc` | The previous task still owes its handoff note, so code edits are frozen | Write the handoff note, then continue |
| `Content contains an em dash or en dash` | A file being written contains one of those characters | Use a plain hyphen-minus |
| `Redirect to /dev/null is denied by project policy` | The command discards output to `/dev/null` | Let the output through, or capture it with `out=$(command 2>&1)` |
| `A redirect to NUL is denied` | The same, in the Windows spelling | The same fix. See [Windows notes](#windows-notes) |
| `Project files must not be written under the runtime home ~/.claude/` | Something tried to store project data in your user directory | Store it in the project instead |

Edits are also denied outside `.agentry/` while a task waits at `plan-approval`. That is the plan gate working: approve or reject the plan and the edits unlock.

## Task and pipeline behavior

| Symptom | Cause | Fix |
|---|---|---|
| Claude keeps working and never asks what next | The stop hook is driving in-flight work. That is the design | For a conversation, switch with `/pipeline-talk` |
| A task is `blocked` | A gate failed more times than `retry_budget` (default 3) | Read the failure, fix the cause, then ask Claude to continue |
| Claude will not start the next task | The previous one still owes a handoff note or a memory review | Let it finish both. They are checked at registration and by the stop hook |
| Claude refuses to implement while you are in plan or talk mode | Those modes write no product code | Switch with `/pipeline-build` or a more specific build command |
| A task will not register | It has no file in `backlog/` or `active/`, its acceptance criteria are empty, or its spec is not approved | Fix the task file or approve the spec |
| You do not see which task waits for you | The status line is not set up, or the session was not restarted after setting it up | Add the `statusLine` snippet from [Where waiting checkpoints show up](pipeline.md#where-waiting-checkpoints-show-up), restart, and look for `Needs you: N`. The reminder on your next prompt works without it |
| You want to drop a task | | Ask Claude to abandon it. It closes the run and moves the file to `done/` |

To see what happened to a task, run `python "$AGENTRY_ROOT/tools/pipeline/events.py" --task <task>`. It prints the journal of transitions, gate results and approvals.

## Windows notes

These apply to Git Bash on Windows and are harmless elsewhere.

- Do not redirect output away. `command 2>NUL` does not discard anything in Git Bash. It creates a file named `NUL` holding the output, and Windows then refuses to let Python delete it. `2>/dev/null` works on that shell, but the project policy denies it. Run the command with no redirect, or capture it: `out=$(command 2>&1)`.
- If a stray `NUL` file already exists, remove it from bash with `rm -f nul NUL`, never from Python.
- Do not pipe the test suite into `head`, `tail`, `grep` or `less`. If a test starts a child process that outlives the suite, the filter never sees the end of input and the gate hangs. Run the suite unpiped or capture it.
- Use forward slashes in bash commands. Use backslashes only for Windows-native tools.

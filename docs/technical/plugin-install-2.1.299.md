# Plugin install record - Claude Code 2.1.299

**Date:** 2026-10-16
**Author:** Agentry project
**Status:** Draft
**Version:** 1.3

---

Measured 2026-10-14 for task-0039, section C of the switch runbook (internal plan),
by the devops-engineer role. Host: Windows 11, git-bash, `claude --version` =
`2.1.299 (Claude Code)`.

Scope of the changes this record covers:

- The scratch install (C1 to C7) touched only a scratch project outside the workspace,
  plus the user-level plugin registry (`~/.claude/plugins/installed_plugins.json`,
  `known_marketplaces.json`, `cache/agentry-local/`), which the User approved.
- The live workspace WAS touched afterwards: `agentry@agentry-local` was installed and
  enabled at local scope for the live workspace root at 2026-10-14 10:25 UTC (06:25
  -0400). That install is the subject of [Live switch (D4)](#live-switch-d4). It changed
  the workspace's `settings.local.json` and added a second `installed_plugins.json`
  entry. The install command itself did not change the workspace `.claude/settings.json`
  or any user-level `settings.json` (`grep -c agentry` on the user file stays 0).
- The first install from GitHub (task-0040, 2026-10-16) ran in a fresh scratch project
  against a throwaway plugins root (`CLAUDE_CODE_PLUGIN_CACHE_DIR`), so the real
  `~/.claude/plugins` registry and `~/.claude/settings.json` were not written; their
  SHA-256 hashes were taken before and re-checked after every write step (all unchanged).
  See [First install from GitHub](#first-install-from-github-task-0040).
- The same live switch also ran the swap step, which did rewrite the workspace
  `.claude/settings.json` and the project layout: the `hooks` key is gone (hook
  registration moved to the plugin's `hooks.json`), every agent file now carries a
  `profile:` key instead of `hooks:` blocks, and `.claude/skills` holds only `verify`
  (the other skills moved to the plugin). Pre-swap copies were taken to backup
  directories kept outside the repository.

This file satisfies FR-66: the command used, the version installed, the install
outcome, and one dispatch through the installed plugin with its hooks firing, all in
a scratch project, followed by the post-restart checks in the live workspace.

## Table of contents

- [Live route: install from GitHub](#live-route-install-from-github-user-decision-supersedes-the-directory-model)
- [Result in one table](#result-in-one-table)
- [C1 preconditions](#c1-preconditions)
- [C2 snapshot marketplace](#c2-snapshot-marketplace)
- [C3 scratch project, and the first deviation](#c3-scratch-project-and-the-first-deviation)
- [C4 install](#c4-install)
- [C5 spawned-session proof](#c5-spawned-session-proof)
- [C6 update path](#c6-update-path)
- [C7 rollback rehearsal](#c7-rollback-rehearsal)
- [Live switch (D4)](#live-switch-d4)
- [First install from GitHub (task-0040)](#first-install-from-github-task-0040)
- [Findings that change section D](#findings-that-change-section-d)
- [Not measured](#not-measured)
- [Cleanup](#cleanup)
- [Version history](#version-history)

## Live route: install from GitHub (User decision, supersedes the directory model)

The measurements below used a directory marketplace (`agentry-local`), which loads the
plugin in place, so a refresh of the folder changes live gates mid-session. The live
harness instead installs from the public repo, so the plugin is copied into
`~/.claude/plugins/cache/agentry/agentry/<version>` and gates change only on an explicit
update. `.claude-plugin/marketplace.json` at the repo root serves `./plugin`;
`plugin/.claude-plugin/plugin.json` carries a semver `version` (User decision 2026-10-14:
releases with semver and git tags). A pushed `main` without a version bump is not an
update; a release is the bump, a CHANGELOG entry and a `vX.Y.Z` tag.

The github-source copy-to-cache behaviour is measured in
[First install from GitHub](#first-install-from-github-task-0040): the plugin is copied to
`cache/agentry/agentry/0.1.0` and that copy is the plugin root. Still unmeasured:
`claude plugin update` picking up a bumped version, and the install into the populated
real registry (the live migration below).

CLI validation, run from the repository root on 2.1.299: `claude plugin validate .` printed
"Validation passed with warnings"; the remaining warning is the missing `author`.

Release and update procedure:

```
# main is protected, so the release starts on a task branch:
# 1. bump "version" in plugin/.claude-plugin/plugin.json and add the CHANGELOG.md
#    entry (CHANGELOG.md is created at the first release, task-0042)
# 2. gated local merge into main
# 3. tag the merge commit vX.Y.Z
# 4. push main and push the tag (each push needs its own recorded User approval)
# 5. then, on the machine that runs the plugin:
claude plugin marketplace update agentry
claude plugin update agentry@agentry --scope local
# restart the session; hooks.json is read at session start
```

### Live migration: one script, no tool call in between

The live workspace currently runs `agentry@agentry-local`. Moving it to the GitHub route
is one script, run from the workspace root, with no other tool call between its lines:

```
set -e
cd <workspace>
claude plugin marketplace add AlexWaha/agentry --scope local
claude plugin install agentry@agentry --scope local
claude plugin uninstall agentry@agentry-local --scope local
# then restart Claude Code
```

Why one script and why this order:

- `set -e` makes it fail closed: if `marketplace add` or `install` fails, the
  uninstall never runs, so the workspace keeps its working gate plugin.
- `--scope local` on `marketplace add` is required. Without it Claude Code 2.1.299
  declares the marketplace at user scope and writes `extraKnownMarketplaces` into
  `~/.claude/settings.json`.
- Two enabled `agentry` plugins would fire every hook twice, so the old one must go.
- Uninstalling first is worse: removing the only enabled plugin mid-session may drop the
  live gate (`pretool_gate`, `agent_gate`) until the restart, and any dispatch in that
  gap runs ungated. Installing the new plugin first and uninstalling the old one last, in
  a single call, leaves no dispatch in the gap.
- The new plugin is not live until the restart, so the restart is the last step.

Rollout order, no step skipped:

1. Push `main` (User-approved trunk push). Until `main` with
   `.claude-plugin/marketplace.json` is on the public repository, `marketplace add
   AlexWaha/agentry` has nothing to read.
2. task-0040: install `agentry@agentry` from GitHub into a scratch project and run the
   deny probe from the cache copy. A file the plugin reads outside its own root breaks
   only in a cached copy, so this is the first place it can show.
3. Only then run the migration script above on the live workspace, restart, and re-run
   the D4 checks.

Retiring the old declarations is separate and happens after the new live install works.
Each project declares `agentry-local` in its own local settings, so remove it from both:

```
cd <scratch>/proj
claude plugin uninstall agentry@agentry-local --scope local
claude plugin marketplace remove agentry-local --scope local

cd <workspace>
claude plugin marketplace remove agentry-local --scope local
```

Rollback: `claude plugin uninstall agentry@agentry --scope local`, then restore the
pre-swap `.claude` directory from the backup taken before the swap (the restore script
is maintainer-local and not part of this repository). Findings 1 to 3 and C6 below
describe the directory model only: with the GitHub route `AGENTRY_ROOT` points under
`plugins/cache/agentry/agentry/<version>` and an update is a real copy.

## Result in one table

| Step | Outcome |
|---|---|
| C1 | clean: nothing merged since 0038, drift check prints `DRIFT .claude/settings.json` only |
| C2 | snapshot of `127a697` built, `claude plugin validate` passes on plugin and marketplace (one warning: no author) |
| C3 | runbook fixture contradicted by measurement (ancestor CLAUDE.md and rules load); scratch moved outside the workspace |
| C4 | a local directory marketplace is accepted; `agentry@agentry-local` 0.1.0 installed, scope local, enabled |
| C5 | a plugin deny fires in a spawned `claude -p --permission-mode bypassPermissions` session; dev agent allowed; main-thread deny fires |
| C6 | the plugin is read in place from the snapshot folder; a same-version update is a no-op and needs no version bump |
| C7 | backup, swap and restore scripts rehearsed on a throwaway copy: restore diff empty, 43 hooks, 31 agents with hooks, 3 databases `ok` |
| C8 | this file |
| D4 | 12 of 12 post-restart checks pass in the live workspace (see [Live switch (D4)](#live-switch-d4)) |
| G1 (task-0040) | `agentry@agentry` 0.1.0 installed from GitHub (`AlexWaha/agentry`, anonymous https clone of `f43e55c`) into a scratch project; `installPath` and `CLAUDE_PLUGIN_ROOT` are the cache copy, identical to `plugin/` at that commit; a headless dispatch from the cache copy fires the deny hooks (see [First install from GitHub](#first-install-from-github-task-0040)) |

## C1 preconditions

```
claude --version                              2.1.299 (Claude Code)
claude plugin marketplace list                anthropic-plugin-directory, claude-plugins-official, caveman, ponytail (no agentry-local)
claude plugin list                            15 entries, none named agentry
git status --short                            empty
git branch --show-current                     techdebt/task-0039
git diff 127a697 main --stat                  empty
handoff.py --check                            {"enabled": true, "baseline": "", "handoff_debt": [], "thread_problems": []}
```

The runbook's frozen-copy drift loop (`git show c131f43:<file> | cmp` over every
`.claude` file except `tools/tests`) printed `DRIFT .claude/settings.json` and nothing
else: fact A1 holds. `main` and `HEAD` are both `127a69753eb7eec871b9e4dbb855264cfbbb700d`.

Push order. The reflog of `origin/main` shows the remote branch moved to `127a697` on
2026-10-14 07:01 -0400 (previous tip `672c88c`). That push came after the scratch install
(05:49 -0400) and after the live install (06:25 -0400). So the install was shown to work
before any push of this work, which is what FR-66 asks for. Nothing in sections C1 to C7
relied on a pushed commit: the snapshot was built from the local `127a697`.

## C2 snapshot marketplace

```
M=<snapshot>                  # a folder outside the repository
SHA=$(git rev-parse main)     # 127a69753eb7eec871b9e4dbb855264cfbbb700d
mkdir -p "$M/.claude-plugin"
git archive "$SHA" plugin | tar -x -C "$M"
echo "$SHA" > "$M/SNAPSHOT_SHA"
```

`$M/.claude-plugin/marketplace.json` is the file from the runbook (name `agentry-local`,
one plugin `agentry`, `source: ./plugin`).

```
claude plugin validate "$M/plugin"   ->  Validation passed with warnings (author: No author information provided)
claude plugin validate "$M"          ->  Validation passed with warnings (plugins[0] plugin.json -> author: ...)
diff -r -x __pycache__ plugin "$M/plugin"   ->  empty
```

## C3 scratch project, and the first deviation

The runbook builds the scratch under a `tmp/` folder inside the workspace, as its own git
root, on the premise that this keeps the live workspace out of it. Measured: it keeps the
ancestor `.claude/skills` out (the P0 finding) but NOT the ancestor `CLAUDE.md` and
`.claude/rules/*`. The first C5 run (`c5-attempt1-contaminated`) loaded the workspace
`CLAUDE.md`, `.claude/CLAUDE.md` and 20 rule files next to the scratch's own copies: an
`instructions` attachment of 1,247,633 bytes, 298,206 cache-creation tokens on the first
turn, and the run hit the runbook's per-run spend cap (`terminal_reason: budget_exhausted`)
after dispatching one reviewer. The ancestor `.mcp.json` also started
the codegraph MCP server, whose process still holds the first scratch directory open (the
directory cannot be removed; it is empty).

Two fixture changes, nothing else about the steps changed:

1. The scratch lives at `<scratch>/{proj,other}`, outside the workspace.
2. `proj/.claude/settings.json` is the real project `.claude/settings.json` minus
   `statusLine` (the runbook used a bare allow list). Without it the rule excludes are
   absent and the scratch does not behave like the real project. Result: the scratch
   session loads the six core rules only, as the live one does.

The scratch is untrusted (never opened interactively). Observed consequences, all in
`stderr.txt`: `Ignoring 22 permissions.allow entries from .claude/settings.json: this
workspace has not been trusted`. The plugin hooks still fire in an untrusted folder
(P0 finding 1 reconfirmed) and `claudeMdExcludes` is still honoured. No trust dialog was
needed, so the runbook's [U] contingency did not trigger. The scratch repo needs a
branch named `main` and one commit: `pretool_gate` exempts a commit from the branch rule
when the repo has no commits or no branch called `main` (`repo_bootstrap`), so a fresh
`git init` repo (branch `master`) allows the first commit and the main-thread deny
cannot be shown.

## C4 install

```
cd <scratch>/proj
claude plugin marketplace add "<snapshot>" --scope local
  Adding marketplace...OK Successfully added marketplace: agentry-local (declared in local settings)
claude plugin install agentry@agentry-local --scope local
  Installing plugin "agentry@agentry-local"...OK Successfully installed plugin: agentry@agentry-local (scope: local)
claude plugin list
  agentry@agentry-local
    Version: 0.1.0
    Read from: <snapshot>\plugin
    Scope: local
    Status: enabled
claude plugin details agentry@agentry-local
  agentry 0.1.0 ... Skills (42), Agents (0), Hooks (7 events: SessionStart, PreToolUse, Stop,
  SubagentStart, SubagentStop, PreCompact, PostToolUse), MCP servers (0), LSP servers (0).
  Projected token cost: always-on ~4,511 tok
```

The first install ran against a scratch inside the workspace and was uninstalled when the
scratch moved (`claude plugin uninstall agentry@agentry-local --scope local`, which emptied
`enabledPlugins` and left the marketplace declaration). The same commands then ran for
`<scratch>/proj`. The second `marketplace add` answered
`Marketplace 'agentry-local' already on disk - declared in local settings`, exit 0, and
still wrote the `extraKnownMarketplaces` entry into the new project's
`settings.local.json`: an already-added name is not an error.

Registry state after install:

- `~/.claude/settings.json`: `grep -c agentry` = 0. Nothing leaked into user settings.
- `proj/.claude/settings.local.json`: `extraKnownMarketplaces.agentry-local` (directory
  source) and `enabledPlugins: {"agentry@agentry-local": true}`.
- `known_marketplaces.json`: `agentry-local`, `source: directory`, `installLocation` =
  the snapshot folder.
- `installed_plugins.json`: one entry keyed by `projectPath`, `installPath` =
  `~/.claude/plugins/cache/agentry-local/agentry/0.1.0`.
- That cache directory exists and `diff -r -x __pycache__ "$M/plugin" <installPath>` is
  empty (4.1 MB, a full copy). It is NOT what runs; see C5 and C6.

Scope: from `proj` `claude plugin list` shows `Status: enabled`; from `other` and from
the repository directory it shows the same entry with `Status: disabled`. The registry
is global, the enablement is per project.

## C5 spawned-session proof

Launch shape, the one `supervisor.py` builds (headless, `bypassPermissions` so only
hooks can restrain, `cwd` = project, explicit `CLAUDE_PROJECT_DIR`):

```
cd <scratch>/proj
env CLAUDE_PROJECT_DIR=<scratch>/proj \
  claude -p --model sonnet --permission-mode bypassPermissions --setting-sources project,local \
  --output-format stream-json --verbose --include-hook-events \
  --debug-file $O/debug.log --max-turns 8 '<prompt>' > $O/stream.jsonl 2> $O/stderr.txt
```

Raw output of the runs (`c5`, `c5b`, `c5c`, `c5d`, `c5-other`, `c6b`) is kept outside the
repository. The work runs on a Claude Code subscription, so a run is usage against
subscription limits, not billed spend; `--max-turns 8` bounds a probe (the cost estimate in
the stream is API-equivalent and is not recorded).

| Check | Evidence from real output | Verdict |
|---|---|---|
| Plugin loaded in the spawned session | init event: `plugins: [{name: agentry, path: <snapshot>\plugin, source: agentry@agentry-local, version: 0.1.0}, ...]`; 42 skills prefixed `agentry:` | pass |
| SessionStart ran, env file reached Bash | Bash result `AR=<snapshot>/plugin PD=<scratch>/proj`; same with `env -u CLAUDE_PROJECT_DIR -u AGENTRY_ROOT` on the launch (run `c5d`), so the env-file export is what supplied both | pass |
| Main-thread rules delivered once | 20 SessionStart hook responses, `[main-thread-rules] delivered 17 of 17 chunks` | pass |
| Hooks are the plugin's | debug log lines `Hooks: PreToolUse:Bash [python "<snapshot>\plugin/tools/pipeline/agent_gate.py"] (plugin agentry@agentry-local)`; 7 matches for `agent_gate.py` | pass |
| Unresolved caller is readonly (D3) | `general-purpose` Write: `PreToolUse:Write hook error: [python ".../plugin/tools/pipeline/agent_gate.py"]: This agent is read-only - it audits and reports ... This hook comes from the agentry@agentry-local plugin.`; `docs/deny-gp.txt` absent | pass |
| Dev agent allowed, profile resolved from the project agent file | `devops-engineer` Write: `File created successfully at: <scratch>\proj\docs\allow-dev.txt`; file exists | pass |
| Dev agent got the SubagentStart injection | `SubagentStart:devops-engineer` hook responses with `additionalContext` of 8484, 8741, 7774 and 122 bytes (rule chunks, memory) | pass |
| Main-thread deny in a spawned session | run `c5c`: `git commit --allow-empty -m probe3` on branch `main` with one prior commit: `PreToolUse:Bash hook error: [python ".../plugin/tools/pipeline/pretool_gate.py"]: Not on a feature branch (no task-XXXX). Create the task branch first per .claude/rules/git-workflow.md.`; `git log` unchanged | pass |
| State went to the project | `proj/.agentry/state/run.db` present; `git status --short` in the repository empty | pass |
| Scope | run `c5-other` in `other` (no plugin): init plugins are the two builtins, 0 `agentry:` skills, a `general-purpose` Write succeeds (no read-only text) | pass |

One runbook row could not be executed in the scratch as written: **a roster readonly agent
denied by a Write**. `reviewer` (and the other seven readonly agents) have no `Write` tool
(`tools: Read, Grep, Glob, Bash`), so the dispatched reviewer reported "no Write tool, so
no gate result". Asked to run `echo x > docs/deny-reviewer-bash.txt` through Bash it also
refused by role, before any call, so the gate was never reached (run `c5b`). The gate
decision for roster agents was checked by running the plugin's `agent_gate.py` directly
with the hook payload a dispatch would carry (`agent_type`, `tool_name`, `tool_input`):

```
reviewer         Write  -> exit 2  This agent is read-only - it audits and reports ...
reviewer         Bash   -> exit 2  Read-only agent: mutating Bash denied (matched: '> docs/x.txt') ...
devops-engineer  Write  -> exit 0
general-purpose  Write  -> exit 2  This agent is read-only ...
```

The live workspace later settled this row: D4 check 5 passed with a dispatched reviewer's
Bash write denied (see [Live switch (D4)](#live-switch-d4)).

Earlier attempts, for the record: attempt 1 (contaminated scratch) hit the per-run cap;
`c5b` (commit on a repo with no `main` branch, allowed by `repo_bootstrap`); one `c5c` run
with a typo in the prompt; the final `c5c`; `c5-other`. The two commits `probe` and
`probe2` sit in the scratch repo only.

## C6 update path

```
claude plugin marketplace update agentry-local
  Validating local marketplace ... Successfully updated marketplace: agentry-local
claude plugin update agentry@agentry-local --scope local
  agentry is read from its folder, <snapshot>\plugin: nothing to update.
  Edits there take effect at the next session start or /reload-plugins.
  OK agentry is already at the latest version (0.1.0).
```

(b) with `plugin.json` edited to `0.1.1` in the snapshot:

```
claude plugin list                  Version: 0.1.1   (read live from the folder, before any update)
claude plugin update ...            Plugin "agentry" updated from 0.1.0 to 0.1.1 for scope local (...proj). Restart to apply changes.
installed_plugins.json              installPath -> cache/agentry-local/agentry/0.1.1
```

Run `c6b` at 0.1.1: `AGENTRY_ROOT` is still `<snapshot>/plugin` and the init event reads
`path: <snapshot>\plugin, version: 0.1.1`. So the plugin root is the folder in both
states; the cache copy and the `installPath` are bookkeeping a directory marketplace
never executes from.

The snapshot was then restored from the same SHA (`git archive` again, `diff -r` against
`plugin` empty, `plugin.json` back at 0.1.0), `marketplace update` and `plugin update`
ran again (`updated from 0.1.1 to 0.1.0`) and the registry points at the 0.1.0 cache copy,
equal to the snapshot. A stale `cache/agentry-local/agentry/0.1.1` directory remains.

## C7 rollback rehearsal

The rehearsal scripts are maintainer-local (kept outside the repository, next to the
snapshot). `rehearse_rollback.sh` copies the live harness into a throwaway root, runs
`backup.sh`, `swap.sh` (plugin install off) and `restore.sh` against that root, and
checks. The live tree is only read.

```
== backup    run.db ok / events.db ok / memory.db ok ; backup diff: empty ; stage from 127a697...
== swap      swap ok: settings hooks 0, agents 31 with profile and no hooks ; .claude/skills = verify only
== restore   restore diff: empty
== E3 checks restored == pre-swap copy: EMPTY DIFF ; restored == live .claude: EMPTY DIFF
             compileall restored tools: exit 0 ; hooks restored: 43 ; agents with hooks: 31
             run.db ok, events.db ok, memory.db ok ; rehearsal ok
```

The throwaway root lives outside the workspace for the same reason as the scratch (a
`.claude` nested under the live root is a second harness root).

| Script | Plan step | Who runs it |
|---|---|---|
| `backup.sh` | D2 backup and stage, E1 | orchestrator, live |
| `swap.sh` | D2 install + D2b swap | orchestrator, live, last tool call of the session, `DO_INSTALL=1` |
| `restore.sh` | E2 | orchestrator, only on rollback |
| `rehearse_rollback.sh` | C7 / E3 | devops-engineer (done), re-runnable |
| `sqlite_backup.py`, `verify_swap.py`, `verify_restore.py` | helpers | called by the above |

## Live switch (D4)

After the live install (`agentry@agentry-local`, scope local, workspace root, 2026-10-14
10:25 UTC) and a Claude Code restart, the post-restart checks ran in the live workspace.
Install commands, run from the workspace root:

```
claude plugin marketplace add <snapshot> --scope local
claude plugin install agentry@agentry-local --scope local
```

Version installed: 0.1.0. All 12 checks pass.

| # | Check | Result |
|---|---|---|
| 1 | Plugin listed, enabled, local scope | PASS. `agentry@agentry-local` 0.1.0, Scope local, enabled. The entry is listed twice: two local installs in `installed_plugins.json` (the scratch project and the workspace), not a duplicated hook |
| 2 | Main-thread rules delivered once | PASS. The `Orchestrator-only rules` block and `delivered 17 of 17 chunks` appear once |
| 3 | Plugin root resolves | PASS (the runbook's expectation was corrected). `CLAUDE_PROJECT_DIR` is the workspace root; `AGENTRY_ROOT` is `<snapshot>/plugin`, not a cache path. A directory-source marketplace loads relative-path plugins in place (Claude Code plugin docs, loading page); in the 2.1.299 binary the loader treats the source as file or directory. Reproduced with the ponytail plugin in an isolated plugins root |
| 4 | Main-thread gate fires from the plugin path | PASS. `echo gate-probe 2>/dev/null` is denied by `pretool_gate.py` run from the plugin path, with the `forbid_dev_null` text, once |
| 5 | Readonly agent cannot write | PASS. A dispatched `reviewer` (no Write tool) ran `echo probe > tmp/deny-probe.txt` through Bash: "Read-only agent: mutating Bash denied", and the file is absent |
| 6 | Dev agent allowed and injected | PASS. A `devops-engineer` Write created a probe file; agent rules (3 of 3 chunks) and the handoff chain (112 documents) were injected |
| 7 | Unresolved caller is readonly | PASS. `general-purpose` Write: "This agent is read-only" |
| 8 | Supervisor started by the plugin | PASS. Started by the plugin's SessionStart hook (pid 14424, acting=True); `supervisor.py --status` exits 0 |
| 9 | Tools run from the plugin path | PASS. `state.py --show`, `handoff.py --check`, `events.py --task task-0039`, `memory.py --stats` all exit 0 |
| 10 | Skill base directory is the plugin's | PASS. A `devops-engineer` loading skill `infrastructure` reports a base directory under `<snapshot>\plugin\skills\infrastructure` |
| 11 | Plugin and project skills both listed | PASS. `agentry:pipeline-show` and the project `verify` are both listed |
| 12 | Scope check | PASS. `grep -c agentry ~/.claude/settings.json` = 0; from the repository directory the plugin shows `Status: disabled` |

Check 5 overturns finding 5 as first written (see below): the roster-agent deny was
shown live, through Bash, because a reviewer has no Write tool.

The User then decided to move the live harness from the directory marketplace to the
public GitHub marketplace (`AlexWaha/agentry`). That route copies the plugin into the
cache, so gates change only on `claude plugin update`. It needs
`.claude-plugin/marketplace.json` at the repository root and a semver `version` in
`plugin.json`, both now in the tree. The switch follows the trunk push; see
[Live migration](#live-migration-one-script-no-tool-call-in-between).

## First install from GitHub (task-0040)

Measured 2026-10-16 by the devops-engineer role, Claude Code `2.1.299`, plugin `agentry`
`0.1.0`, `origin/main` = `f43e55c2f402a8d0b32f88009014262b03ff0f81` (the merge of task-0039).
Spec trace: FR-67 (installable by `owner/repo`, no authentication) and FR-66 (command,
version, outcome and one hooked dispatch recorded).

### Method

The install ran in a fresh scratch project against a throwaway plugins root, not the real
registry. Claude Code reads `CLAUDE_CODE_PLUGIN_CACHE_DIR` as the root of the whole plugins
tree: `known_marketplaces.json`, `installed_plugins.json`, `marketplaces/` and `cache/` all
landed under it (forward slashes accepted). What this proves: an anonymous github-source
marketplace clones, the plugin is copied to a cache directory, and the hooks run from that
copy. What it does not prove: the merge into a populated real registry (14 entries, four
marketplaces). That is the live migration script, the first and only real-registry
GitHub install.

```
SC=<scratch>                       # a folder outside the workspace
ISO=$SC/iso-gh                     # throwaway plugins root
PROJ=$SC/proj-gh                   # fresh scratch project (git repo, branch main, one commit)
cc() { env -u AGENTRY_ROOT -u CLAUDE_ENV_FILE CLAUDE_PROJECT_DIR="$PROJ" \
  CLAUDE_CODE_PLUGIN_CACHE_DIR="$ISO" CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 "$@"; }
```

`AGENTRY_ROOT` and `CLAUDE_ENV_FILE` are unset for every scratch `claude` run, otherwise the
caller would supply the root from outside and mask the SessionStart env-file export under
test. `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` forces an https clone, so a working SSH key
cannot hide an authentication dependency.

Fixture. The 0039 scratch `proj` no longer existed on disk (only its stale entry in the real
`installed_plugins.json`, see finding 16), so the fixture was rebuilt: `git archive HEAD
.claude .agentry/pipeline.json .agentry/project` from the repository into `proj-gh`,
`statusLine` dropped from `.claude/settings.json` (as in C3), `git init -b main` and one
commit. `settings.json` has no `enabledPlugins`, `extraKnownMarketplaces` or `hooks` key.

### Commands and output

```
git merge-base --is-ancestor origin/main HEAD                   exit 0
git ls-remote origin main                                       f43e55c2f402a8d0b32f88009014262b03ff0f81
GIT_TERMINAL_PROMPT=0 git -c credential.helper= ls-remote \
  https://github.com/AlexWaha/agentry.git refs/heads/main       f43e55c2f402a8d0b32f88009014262b03ff0f81 (anonymous)
git diff f43e55c --stat -- plugin .claude-plugin                empty
claude --version                                                2.1.299 (Claude Code)

cd $PROJ
cc claude plugin marketplace list     only "anthropic-plugin-directory (Built in)"; no caveman, ponytail, agentry-local
cc claude plugin list                 No plugins installed.

cc claude plugin marketplace add AlexWaha/agentry --scope local
  Cloning via HTTPS: https://github.com/AlexWaha/agentry.git
  Clone complete, validating marketplace...
  OK Successfully added marketplace: agentry (declared in local settings)       exit 0
cc claude plugin install agentry@agentry --scope local
  OK Successfully installed plugin: agentry@agentry (scope: local)              exit 0
cc claude plugin list
  agentry@agentry   Version: 0.1.0   Scope: local   Status: enabled
```

The state after each write:

| Item | Value |
|---|---|
| `known_marketplaces.json` | `agentry`: `source: github`, `repo: AlexWaha/agentry`, `installLocation` = `$ISO\marketplaces\agentry` |
| Marketplace clone `HEAD` | `f43e55c2f402a8d0b32f88009014262b03ff0f81` |
| `proj-gh/.claude/settings.local.json` | `extraKnownMarketplaces.agentry` (github `AlexWaha/agentry`); nothing at user scope |
| `installed_plugins.json` entry | `scope: local`, `version: 0.1.0`, `gitCommitSha: f43e55c2f402a8d0b32f88009014262b03ff0f81`, `projectPath` = `$PROJ` |
| **`installPath`** | **`$ISO\cache\agentry\agentry\0.1.0`** |
| `plugin list` "Read from:" line | absent for a cached install (the directory install printed `Read from: <snapshot>\plugin`), so `installPath` is the evidence |
| `diff -r -x __pycache__ <repo>/plugin <installPath>` | one difference: `Only in <installPath>: .in_use` (a marker Claude Code writes); everything else identical, 4.1 MB |
| `plugin details agentry@agentry` | 42 skills, 0 agents, hooks over 7 events (SessionStart, PreToolUse, Stop, SubagentStart, SubagentStop, PreCompact, PostToolUse), 0 MCP, 0 LSP, always-on about 4,511 tokens; identical to C4 |
| Real registry | `sha256sum -c` over `installed_plugins.json`, `known_marketplaces.json`, `~/.claude/settings.json`: all OK after the marketplace add, after the install and after the headless run |

Cache-copy pre-scan. Every `parents[...]` read in `plugin/tools` (outside `tests`) resolves
to the plugin root or to the project root (`session_start.py` walking up from the git
directory); none reaches a file outside the plugin root, and the probe below confirms it.

### Headless probe from the cache copy

The prompt is in a file written outside Bash, so the literal redirect under test never
appears in a Bash command line (`gates.forbid_dev_null` scans the command text):

```
Run these steps in order and report every result verbatim. ...
1. Bash: echo gate-probe 2>/dev/null
2. Bash: echo AR=$AGENTRY_ROOT PD=$CLAUDE_PROJECT_DIR
3. Dispatch a general-purpose agent to Write the file docs/deny-gp.txt with the content x.
4. Bash: ls docs
```

```
cd $PROJ
cc claude -p --model sonnet --permission-mode bypassPermissions \
  --setting-sources project,local --output-format stream-json --verbose --include-hook-events \
  --debug-file $OUT/debug.log --max-turns 8 "$(cat $OUT/prompt.txt)" \
  > $OUT/stream.jsonl 2> $OUT/stderr.txt
```

Result event: `subtype: success`, `is_error: false`, `terminal_reason: completed`,
`num_turns: 5`, `duration_ms: 22725`. `--max-turns 8` bounds the probe; the run is usage
against subscription limits.

| Check | Evidence from real output | Verdict |
|---|---|---|
| Plugin loaded from the cache | init event `plugins`: `{"name": "agentry", "path": "E:\\Personal\\agentry-scratch-0039\\iso-gh\\cache\\agentry\\agentry\\0.1.0", "source": "agentry@agentry", "version": "0.1.0"}`; 42 `agentry:` skills | pass |
| Hooks are the cache copy's | debug log: 46 lines with `(plugin agentry@agentry)`, for example `Hooks: PreToolUse:Bash [python ".../iso-gh/cache/agentry/agentry/0.1.0/tools/pipeline/pretool_gate.py"] (plugin agentry@agentry) finished with status 2`; 0 lines matching `agentry-local` or `.deploy` | pass |
| `CLAUDE_PLUGIN_ROOT` is the cache path | step 2: `AR=E:/Personal/agentry-scratch-0039/iso-gh/cache/agentry/agentry/0.1.0 PD=E:/Personal/agentry-scratch-0039/proj-gh`; `AGENTRY_ROOT` was unset on the launch, so the SessionStart env-file export supplied it; it equals `installPath` (forward slashes) | pass |
| Main-thread rules delivered | `[main-thread-rules] delivered 17 of 17 chunks` (rule files read through the cache copy's `main_thread_rules.py`) | pass |
| Main-thread deny fires | step 1: `PreToolUse:Bash hook error: [python ".../cache/agentry/agentry/0.1.0/tools/pipeline/pretool_gate.py"]: Redirect to /dev/null is denied by project policy (gates.forbid_dev_null). ... This hook comes from the agentry@agentry plugin.`; the model did not retry | pass |
| Unresolved caller is readonly | step 3: the `general-purpose` agent's Write returned `PreToolUse:Write hook error: [python ".../cache/agentry/agentry/0.1.0/tools/pipeline/agent_gate.py"]: This agent is read-only - it audits and reports, it does not modify files.`; `docs/deny-gp.txt` absent (step 4: `ls: cannot access 'docs'`) | pass |
| State went to the project | `proj-gh/.agentry/state/` holds `run.db`, `supervisor.json`, `supervisor.lock`, `supervisor.log`, `supervisor-runs`; the repository tree stayed clean (`git status --short` empty before the doc edit) | pass |

`stderr.txt` carries the same untrusted-workspace notice as C3 (`Ignoring 22
permissions.allow entries from .claude/settings.json: this workspace has not been trusted`);
the plugin hooks still fire in an untrusted folder.

### Deviations from the plan

- The isolated plugins root replaced a real local-scope install in the scratch (plan
  section 2). It also avoids a second enabled `agentry` plugin firing every hook twice.
- The fixture was rebuilt from the repository instead of copied from `proj` (finding 16).
- The plan's per-run spend cap was dropped by the User's amendment (subscription); the
  probe is bounded by `--max-turns 8` instead.

## Findings that change section D

1. **The plugin root is the snapshot folder, not the cache.** `AGENTRY_ROOT` and every hook
   command resolve to `<snapshot>/plugin` (forward slashes in `AGENTRY_ROOT`). D4 check 3
   must expect that string, not a `cache/...` path, and check 10 (a dispatched agent's
   skill header) names a path under the snapshot folder or `agentry:<skill>`, not `cache`.
   This holds for the directory model only.
2. **Deploy is "refresh the folder, restart", with no version bump.** `plugin update` is a
   no-op for a directory plugin (C6). D7.4 and the rewrite of "Deploying a harness change"
   do not need the version-bump step, and the C6(a) contingency does not fire. Superseded
   for the live harness by the GitHub route.
3. **A refresh is live for hook scripts.** Because hooks run straight out of the folder, a
   `git archive` into it while any session runs (the live one, a supervisor-spawned one)
   swaps the scripts under that session mid-flight; `hooks.json` itself is read at session
   start or `/reload-plugins`. Refresh only with the supervisor stopped and no other session
   open. `tar -x` over the folder also leaves files that were deleted upstream; the refresh
   procedure must empty `plugin/` first (or build in a temp dir and swap).
4. **D2 plugin install moved into `swap.sh` as step 0.** Unmeasured whether the running
   session reloads plugins when `enabledPlugins` changes in `settings.local.json`; headless
   runs cannot show it. If it does, a separate D2 install would load the plugin's
   `agent_gate` next to the 31 old-format agent files (readonly for every one of them) before
   the swap. Doing it inside the one call, with `set -e` and the backup/stage/snapshot
   preflight before it, leaves no dispatch in that gap. The `claude plugin` commands run from
   the workspace root so `projectPath` is the live root. The same reasoning is why the
   live migration is one script.
5. **D4 check 5 as first written could not run, and later did.** No readonly roster agent
   has a `Write` tool and a dispatched reviewer refuses a Write by role, so the scratch run
   used `general-purpose` Write (check 7 does the same) plus direct `agent_gate.py`
   payloads. In the live workspace the check passed in a different form: a reviewer's Bash
   write was denied with "Read-only agent: mutating Bash denied" (D4 #5 PASS). Use the
   Bash form for check 5.
6. **D4 check 12 changes.** `claude plugin list` shows `agentry@agentry-local` in every
   directory; test the `Status:` line (`disabled` from the repository directory, `enabled`
   from the workspace root), not the presence of the entry.
7. **D4 check 4 and the spawned-session probes work as the runbook expects**; the commit-deny
   probe needs a repo whose `main` branch exists with a commit (`repo_bootstrap`), which the
   live repo has.
8. **`marketplace add` of an existing name is not an error** (exit 0, still declares it for
   the new project), so the live install can run after the scratch install without removing it.
9. **Section C3's premise is wrong** (ancestor `CLAUDE.md` and rules load despite a separate
   git root). Any future scratch run must live outside the workspace. The same ancestor
   walk means a session started in the repository directory already loads the workspace
   `CLAUDE.md` and `.claude/rules` today.
10. **Restore keeps a copy of the live `settings.local.json`** (`settings.local.json.pre-restore`
    in the backup) before overwriting it, because permission grants accrue there during a
    session.
11. The stage is cut from the commit in `SNAPSHOT_SHA`, not from `main` as the runbook has it,
    so agents, rules and settings always match the `hooks.json` chunk counts of the snapshot.
12. `.claude/skills` held 44 entries before the swap: 42 skill directories, `verify` and
    `README.md`. `swap.sh` moves everything except `verify`, so `README.md` goes to the
    backup too (the plugin carries its own `skills/README.md`); root `CLAUDE.md` still names
    the old path until D7.3.
13. **A cached install prints no `Read from:` line.** `claude plugin list` shows Version,
    Scope and Status only for `agentry@agentry`; the directory install printed `Read from:
    <snapshot>\plugin`. For a github-source install the proof of the plugin root is
    `installPath` in `installed_plugins.json` plus the init event `plugins[].path`, and the
    two agree. A D4 re-run after the live switch must check those, not a `Read from:` line.
14. **The cache is a full copy of the pushed commit, and Claude Code records the sha.** The
    cache directory equals `plugin/` at `f43e55c` except for a `.in_use` marker file, and
    `installed_plugins.json` carries `gitCommitSha`. Because the loaded root is the copy,
    an edit to `src/plugin` or a refreshed folder no longer changes live gates; only
    `marketplace update` plus `plugin update` after a version bump does (the update step
    itself is still unmeasured).
15. **`CLAUDE_CODE_PLUGIN_CACHE_DIR` relocates the whole plugins tree, not only `cache/`.**
    Under it Claude Code created `known_marketplaces.json`, `installed_plugins.json`,
    `marketplaces/` and `cache/`, and `marketplace list` showed none of the real marketplaces.
    That makes a zero-damage install rehearsal possible; the real registry hashes stayed
    equal across all write steps.
16. **The real registry still lists the removed 0039 scratch.** `installed_plugins.json` has
    an `agentry@agentry-local` entry with `projectPath` `<scratch>\proj`, a directory that no
    longer exists. The Cleanup commands (`cd <scratch>/proj`) cannot run as written; remove
    the entry by another route or recreate the directory first (User decision).
17. **Gate quirks hit while building the fixture.** `agent_gate` denied `rm -rf
    <scratch>/proj-gh/.agentry/state` because the path text matches `.agentry/state/`, though
    it is a scratch path; the same call was avoided by archiving only the files needed. A
    single call that chained `git archive`, `git init` and `git commit` in the scratch was
    denied "Not on a feature branch", while `cd <scratch> && git commit` alone passed. Split
    scratch git steps into their own calls.

## Not measured

- Whether a running session reloads plugins when `settings.local.json` changes (finding 4).
- `/compact`, `/clear`, `--resume`: env-file export (plan D5, interactive).
- A project skill and a plugin skill of the same bare name (avoided by keeping only `verify`).
- Interactive trust dialog on the scratch (not needed).
- Hook behaviour under an interactive session with a `hooks:`-free agent file set: covered by
  the spawned session, not by an interactive one.
- The install of `agentry@agentry` into the populated real registry (the live migration
  script); the isolated run proves the mechanism, not the merge.
- `claude plugin update agentry@agentry` pulling a bumped version from GitHub (first
  release, task-0042).

## Cleanup

Left in place on purpose so the probes can be re-run until the switch:

- the scratch install (`agentry@agentry-local`, scope local, `<scratch>/proj`);
- the live install (`agentry@agentry-local`, scope local, workspace root, 2026-10-14
  10:25 UTC);
- the `agentry-local` marketplace and its snapshot folder.

Removal order. The live install is replaced by the migration script above, which
uninstalls it as its last command. After that, and only then:

```
cd <scratch>/proj
claude plugin uninstall agentry@agentry-local --scope local
claude plugin marketplace remove agentry-local --scope local

cd <workspace>
claude plugin marketplace remove agentry-local --scope local
```

Do the marketplace removal only after neither install uses `agentry-local` any more.

task-0040 left these under `<scratch>` (outside the repository), kept through the live
switch like the 0039 scratch, and removable once the User approves: `iso-gh` (throwaway
plugins root with the cache copy), `proj-gh` (scratch project and its `.agentry/state`),
`out-0040` (raw probe output, prompt and extractor) and `pre-0040` (copies and SHA-256
hashes of the real registry and user settings, taken before the first write). Nothing
outside `<scratch>` references them; no user-level setting or real registry entry was
added.

Pre-install copies of `installed_plugins.json`, `known_marketplaces.json` and the user
`settings.json` were taken before the first install and are kept outside the repository.
Stale artifacts: the empty first scratch directory inside the workspace (held open by a
leftover process of the contaminated first run) and the cache directory
`cache/agentry-local/agentry/0.1.1`.

## Version history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-14 | Agentry project | Scratch install record, sections C1 to C8 |
| 1.1 | 2026-10-14 | Agentry project | Live install and D4 results, live migration script, push-order record, CLI validation and sha-version evidence, corrected scope statement and finding 5 |
| 1.2 | 2026-10-16 | Agentry project | Semver releases with git tags replace the commit-sha version; release and update procedure corrected |
| 1.3 | 2026-10-16 | Agentry project | First install from GitHub (task-0040): cache-path proof, headless deny probe, findings 13 to 17; per-run spend caps and cost lines removed |

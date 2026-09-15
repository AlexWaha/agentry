# Per-repo nested CLAUDE.md

**Date:** 2026-09-15
**Author:** devops-engineer
**Status:** In Review
**Version:** 1.0

---

## Table of Contents

- [Finding: nothing to move today](#finding-nothing-to-move-today)
- [Where the file lives](#where-the-file-lives)
- [When Claude Code loads it](#when-claude-code-loads-it)
- [What belongs in it](#what-belongs-in-it)
- [Removing a rule from the core list is not enough](#removing-a-rule-from-the-core-list-is-not-enough)
- [Limits worth knowing](#limits-worth-knowing)
- [How this was measured](#how-this-was-measured)

---

## Finding: nothing to move today

FR-31 asks that a repo-specific stack rule live in its repo's nested
`CLAUDE.md` rather than in the root set every context pays for. The audit found
no such rule. All 8 rules in the core set of `.claude/CLAUDE.md` were read in
full against that criterion:

| Rule | Repo-specific? | Evidence |
|---|---|---|
| `quality-standard.md` | No, but read the caveat below | Verification discipline, pre-flight checks, punctuation and authorship bans. 4 distinct placeholders across 7 lines (`{{BUILD_CMD}}`, `{{LANG}}`, `{{FRAMEWORK}}`, `{{VERSION}}`, 9 occurrences), all deferring to `.agentry/project/stack.md` by name. Its "FORBIDDEN: Hardcoded Fallback Locale" section (lines 11-13) is the most stack-specific content anywhere in the core set: a backend config key, a frontend `DEFAULT_LOCALE` constant, a hardcoded `'en'` literal, ORM vocabulary, and a cross-reference into `i18n.md`, which is one of the 12 excluded rules. It still scores No because FR-31's unit is the whole rule file and the rest of the file is workspace policy. |
| `communication.md` | No | Language, tone, report format. One non-stack placeholder (`{{COMM_LANG}}`, line 10), zero stack tokens, zero path references into any repo. |
| `git-workflow.md` | No | Branch naming, gate order, checkpoints. 3 distinct placeholders across 4 lines, deferring to `stack.md` in the file's own opening note, and its "Multi-repo workspaces" section is workspace-level by construction: it is the rule that tells you to run git per sub-repo. Its Deploy Actions table (lines 418-422) carries five concrete `[EXAMPLE - Laravel]`, `[EXAMPLE - Composer]` and `[EXAMPLE - Node]` commands; they score No because they are labelled examples of a category, not instructions this workspace executes, and the categories themselves are stack-neutral. |
| `task-creation.md` | No | Task file structure and the cross-layer impact check. Zero stack tokens. |
| `code-retrieval.md` | No | Codegraph query discipline. The index is per project root, not per repo. |
| `self-learning.md` | No | Lesson capture and the distill-and-stamp loop. Zero stack tokens. |
| `pipeline.md` | No | The stage map. 3 distinct placeholders across 3 lines, all in the exit-gate column, resolving per project from `stack.md` and `.agentry/pipeline.json`. |
| `orchestration.md` | No | The orchestrator's operating loop. Zero stack tokens. |

**Why the set is empty:** this workspace contains exactly one git repository,
`src/`. The workspace root is not a repo. With one repo, "specific to one repo"
and "applies to the whole workspace" describe the same set of files, so no rule
can satisfy the criterion even in principle. The emptiness is a property of the
current layout, not a judgment that the rules were reviewed and found
acceptable.

**What would change it:** a second repository appearing beside `src/` - a
separate frontend, a service split out, a vendored SDK with its own toolchain.
At that moment any rule whose commands, ports, framework conventions or
directory layout belong to one of them stops being a workspace rule, and this
document is the procedure for moving it. `repos{}` (task-0028) will make the
split explicit in config; it is not a precondition for using the mechanism.

The audit is auditable rather than final: re-run it per rule against the table
above whenever the core list or the repository count changes. Two rows carry
stack-shaped content that a re-runner must weigh rather than skim, and the
verdict column alone will not warn them:

1. **`quality-standard.md`, the fallback-locale section.** Re-examine this
   first. It is a frontend and backend implementation rule sitting inside a
   policy file, and it points into an excluded rule. In a workspace where one
   repo owns the locale handling, this fragment belongs to that repo, and the
   honest fix is to move the fragment rather than the file.
2. **`git-workflow.md`, the Deploy Actions examples.** Harmless while they stay
   labelled examples. If a project ever replaces them with its own real
   commands, the row changes verdict.

FR-31's unit is the whole rule file, so neither flips today. A rule file that is
mostly workspace policy with a repo-specific fragment inside it is a
split-the-fragment problem, not a move-the-file problem.

## Where the file lives

Both of these load, and either spelling works:

```
<repo>/CLAUDE.md
<repo>/.claude/CLAUDE.md
```

`<repo>/CLAUDE.local.md` also loads and is the personal, uncommitted variant.
In this workspace that means `src/CLAUDE.md` and `src/.claude/CLAUDE.md`, both
of which already exist and are already loaded this way whenever an agent reads a
file under `src/`.

A nested directory's `.claude/rules/` directory is walked too, so a repo may
carry rule files of its own, not just a `CLAUDE.md`. Measured, twice: those rule
files attach with no include list naming them, and they are filtered by
`claudeMdExcludes` exactly as the workspace-level ones are. A repo rule file
must therefore carry a name that no exclude entry matches.

## When Claude Code loads it

On a file read, not at session start, and not on a shell command.

When the Read tool reads a file, Claude Code walks from that file's directory
upward to the session's working directory and loads the memory files of every
directory in between. The working directory itself is excluded from this walk -
its `CLAUDE.md` is already loaded by the normal session-start path. Directories
*above* the working directory are visited by a separate branch that loads only
glob-scoped `.claude/rules/` entries, never a `CLAUDE.md`.

Consequences to plan around:

- A repo's nested `CLAUDE.md` costs nothing in a session that never touches that
  repo. That is the entire point of the mechanism.
- It arrives mid-session, after the agent has started work, not in the startup
  context.
- Each path is attached at most once per session. Reading a second file in the
  same directory does not re-attach it.
- It works inside a subagent dispatch, not only on the main thread.
- Reading a file with `cat`, `sed` or any other shell command does NOT attach
  it. Only the file-reading tools push the trigger.
- Editing the file requires reading it, so an edit attaches it the same way.

## What belongs in it

Facts true of that repository and false, or meaningless, one directory over:

- Build, test, lint and run commands for that repo's toolchain.
- Framework and language conventions specific to that stack.
- Directory layout, entry points, generated paths.
- Environment variable names and port assignments the repo owns (names and
  value shapes only, never real secrets).
- Local gotchas: a flaky generator, a required codegen step, a platform quirk.

What does not belong: anything the workspace enforces regardless of repo -
git workflow, task and pipeline discipline, communication and quality standards,
security posture. Those stay in the core set or in per-agent `rules:` keys,
because an agent must obey them before it has read any file at all.

## Removing a rule from the core list is not enough

This is the finding task-0014 paid for and it applies unchanged here. Deleting a
rule's line from `.claude/CLAUDE.md` does not stop the rule loading: Claude Code
also walks `.claude/rules/` at session start and picks the file up regardless of
any list. Moving a rule to a repo takes three edits:

1. Remove its `@rules/<name>.md` line and its FR-30 table row from
   `.claude/CLAUDE.md`.
2. Add `"**/.claude/rules/<name>.md"` to `claudeMdExcludes` in
   `.claude/settings.json`. This is the edit that actually stops the walk.
3. Put the content in the repo's nested `CLAUDE.md`.

Step 2 is not optional and is not implied by step 1. The exclude patterns match
by filename, so an identically named rule under a user's own `~/.claude/rules/`
is excluded too.

**Step 3 says put the content there, not move the file, and the difference is
not cosmetic.** Relocating `<name>.md` into `<repo>/.claude/rules/` looks
attractive because the nested walk does read that directory. It does not work in
combination with step 2: `**/.claude/rules/<name>.md` matches the relocated path
exactly as it matches the user-level one, so the rule would then load nowhere at
all - the mirror image of the failure this section exists to prevent. Moving the
file is only viable if it is also RENAMED, so that no `claudeMdExcludes` entry
matches its new name. Copying the content into the repo's `CLAUDE.md` avoids the
collision entirely, which is why it is the procedure given here.

**Timing differs by path, and this was measured rather than assumed.** A
`claudeMdExcludes` change does not alter what a running session already loaded
at startup: that instruction list is cached and rebuilt on seven events, none of
which is a file edit, so the session-start set needs a restart. The nested
attach path is a different story: an entry added mid-session governed the very
next nested read, so the list is not frozen at session start on this path.
A nested `CLAUDE.md` is likewise read from disk at attach time, so creating or
editing one takes effect on the next read with no restart.

## Limits worth knowing

- **The attachment is a feature-gated code path.** A flag named
  `tengu_paper_halyard`, default off in build 2.1.269, filters project-scoped
  and local-scoped entries out of the nested-directory result. Off, nested
  `CLAUDE.md` files attach as described here. Were it turned on, only rule files
  would survive the filter. Re-measure before assuming this document still holds
  on a newer build.
- **`CLAUDE_CODE_DISABLE_CLAUDE_MDS`** disables the whole mechanism, and
  `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` is a separate path that loads
  extra directories at session start.
- **Nothing enforces this.** No hook and no test checks that a repo-specific
  fact sits in a nested `CLAUDE.md` rather than in the core set. The check is the
  audit table at the top of this document.

## How this was measured

Asserting this mechanism would have repeated the mistake task-0014 uncovered, so
it was measured twice, independently.

**In the binary** (`2.1.269`, the build in use). The directory computation
splits a triggering file path into the directories between the working directory
and the file, and the ancestors above the working directory; the first group is
loaded with `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md` and a
`.claude/rules/` walk, the second with glob-scoped rules only. The per-path
attach guard and the trigger reason string `nested_traversal` sit in the same
loader. The trigger itself is pushed by the file-read tool paths (text, notebook
and image reads), which is why a shell read does not fire it.

**In this workspace, unplanned - and it proves less than it appears to.**
Opening `src/.claude/CLAUDE.md` with the Read tool attached `src/CLAUDE.md` and
the 8 core rule files out of `src/.claude/rules/`, with none of the 12 excluded
rules present. The tempting reading, that this shows `claudeMdExcludes`
filtering the nested walk, does not survive inspection: the 8 arrived in the
order of the `@rules/` list inside `src/.claude/CLAUDE.md`, not in alphabetical
directory order, which is the signature of the live include rather than of a
walk. The 12 are absent from that list to begin with, so their absence from the
context is explained without reference to the exclude mechanism at all. The
observation discriminates nothing and is recorded here only because the claim it
seemed to support was briefly believed.

**The probe that does discriminate.** Two further probes settled it. Probe C
placed a rule file named in neither the `@rules/` list nor `claudeMdExcludes`
into `tmp/probe-fr31c/.claude/rules/`, in a directory holding no `CLAUDE.md` at
all; reading a file two levels below it attached that rule. So the nested walk
loads rule files on its own, with no include list involved anywhere. Probe D
then put two freshly named rule files side by side in one nested
`.claude/rules/`, added ONLY the first to `claudeMdExcludes`, and read a single
target below them: the control attached, the excluded one did not. That is the
discriminating result, and it carries a second fact with it - the exclude entry
was added minutes before the read, in the same session, so an entry added
mid-session governed the very next nested read and the list is therefore not
frozen at session start on this path.

**Live, in this session.** A probe directory `tmp/probe-fr31/` was given a
`CLAUDE.md` carrying a unique marker and a target file two levels down. Reading
the target with the Read tool attached the marker file. A second probe,
`tmp/probe-fr31b/`, used the `.claude/CLAUDE.md` spelling: reading its target
with `cat` attached nothing, and reading the same file with the Read tool
attached the marker. A third file in that directory attached nothing further,
confirming the once-per-session guard. Both probes ran inside a subagent
dispatch. The probe directories are scratch and are not committed.

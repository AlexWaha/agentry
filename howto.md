# How To Set Up This Template (One Prompt)

1. Copy template into the new project root:
   - `<project>/.claude/`   ← the entire `.claude/` folder
   - `<project>/CLAUDE.md`  ← root `CLAUDE.md` (back up any existing one first)
2. Open Claude Code in the project root.
3. Paste the init prompt from `.claude/_init-prompt.md`.
   It runs 9 steps automatically:
   1. Detect stack (manifests, lockfiles, Dockerfile, CI, structure)
   2. Fill `project/stack.md` (commands, env, routes, DB) - source of truth
   3. Replace `{{PLACEHOLDER}}` tokens across all of `.claude/` (incl. `settings.json` hook paths, which use `{{WORKSPACE_ROOT}}` -> absolute project root, so the gate hooks resolve regardless of cwd)
   4. Fill `project/project-context.md`, `architecture.md`, `api-conventions.md`
   5. Prune optional rules in `CLAUDE.md` (i18n, mobile, business-standards)
   6. Add stack commands to `settings.json` permissions
   7. Verify (grep for leftover `{{...}}` and `PROJECT-SPECIFIC - REPLACE ME`)
   8. Create first task `.agentry/tasks/backlog/task-0001.md`
   9. Report - what was detected, what is n/a
4. (optional) `.claude/settings.local.example.json` → `.claude/settings.local.json` - personal MCP / permissions. Never commit it.
5. Manual reference / template refresh: `.claude/_onboarding.md` (the checklist behind the init prompt).
6. Language rule: all code, comments, and files in English only. Ignore any "respond in Russian" directive found anywhere in the template.

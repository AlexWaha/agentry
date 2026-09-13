# Fleet onboarding + memory migration runbook (per project)

You are running automated onboarding + memory migration for ONE project as part of
a fleet rollout of the AI-team-universal build. You are given a single variable:

    ROOT = the project root (its .claude is at ROOT/.claude)

Hard rules:
- Work ONLY inside ROOT. Do NOT touch git (no commit, branch, stash, checkout).
- All written content in ENGLISH only.
- NEVER use em dash or en dash. Use only regular hyphen-minus.
- No AI-attribution anywhere.
- On Windows/Git-Bash, NEVER use unix redirects (`2>/dev/null`, `>/dev/null`);
  run commands plainly. Use forward-slash absolute paths.
- SOURCE template (read-only reference):
  d:/Work/AlexWaha.com/AI-team/AI-team-universal/.claude
  Read source `.claude/_init-prompt.md` (Phase A + Phase B) and
  `.claude/memory/README.md` (L1/L2/L3 entry formats) for the full contract.
- The target's gate hooks do NOT fire in this session; you may Edit/Write target
  paths freely.

## Steps (in order)

1. STATE. Read ROOT/.claude/project/stack.md. If it still contains `{{...}}`
   tokens or "PROJECT-SPECIFIC - REPLACE ME" markers, this project is FRESH: run
   full Phase A - detect the stack (manifests, lockfiles, Dockerfile/compose, CI,
   folder structure; note *.local projects are usually OpenCart/PHP on OpenServer,
   not Docker) and fill project/stack.md + project-context.md + architecture.md +
   api-conventions.md, removing every marker. If stack.md already holds real
   content, this project is ALREADY-ONBOARDED: do NOT overwrite project/*.md.

2. PLACEHOLDERS (do this the CHEAP, interruption-proof way - do NOT hand-edit
   30 files one by one):
   a. First make sure the "Placeholder values for CLAUDE.md" table in
      project/stack.md has a row with a REAL value for EVERY token the template
      uses - not just the 18 default rows. The template also uses these
      stack-derivable tokens; ADD a row for each that applies to this project
      (use the detected value, or "none"/"n/a" where it genuinely does not apply -
      never leave `[PROJECT-SPECIFIC ...]` or a `{{...}}` in the value column):
      `{{PKG_MANAGER}}`, `{{CACHE_BACKEND}}`, `{{BACKEND_RUNTIME}}`,
      `{{FRONTEND_RUNTIME}}`, `{{FRONTEND_STACK}}`, `{{FRONTEND_TEST_CMD}}`,
      `{{BACKEND_TEST_CMD}}`, `{{MIGRATION_TOOL}}`, `{{WEB_SERVER}}`,
      `{{TYPECHECK_CMD}}`, `{{PROJECT_DB_NAME}}`, `{{PROJECT_DB_USER}}`,
      `{{EXTERNAL_SERVICE}}`, `{{FRAMEWORK_DOCS_URL}}`, `{{COMM_LANG}}`,
      `{{SRC_DIR}}`, `{{RESOURCE}}`, `{{MODULE_A}}`, `{{MODULE_B}}`,
      `{{MODULE_C}}`. Each row format exactly: ``| `{{TOKEN}}` | value |``.
      `{{COMM_LANG}}` = the language agents report to the CEO in (English here).
      `{{PRODUCT_DESCRIPTION}}` / `{{STACK}}` should already be in the table.
   b. Then RUN the deterministic substituter (one command, replaces every mapped
      token across .claude/ + root CLAUDE.md at once):
      `python "d:/Work/AlexWaha.com/AI-team/AI-team-universal/.claude/tools/setup/substitute_tokens.py" --target ROOT`
      Read its REPORT line: `remaining_tokens` lists any token still present.
   c. Only for whatever `remaining_tokens` still reports (usually semantic slots
      the table cannot express, e.g. a module name inside prose), hand-edit those
      few spots. Runtime tokens `{{TASK}}`, `{{TITLE}}`, `{{DATE}}`, `{{FILES}}`,
      `{{VERSION}}`, `{{MERGE_COMMIT}}`, `{{PLACEHOLDER}}`, `{{PLACEHOLDERS}}` that
      appear only inside task/handoff templates or system-describing prose are
      INTENTIONAL - leave them.
   SKIP files (intentional runtime tokens): `_onboarding.md`, `_init-prompt.md`,
   `tasks/templates/handoff-template.md`. Never write a machine-local absolute
   path into settings.json or agents/*.md - hooks keep using `$CLAUDE_PROJECT_DIR`.

3. PRUNE optional rule imports in the root CLAUDE.md: keep `rules/i18n.md` only if
   multilingual, `rules/mobile.md` only if a mobile app exists,
   `rules/business-standards.md` only if business agents are used. Remove the
   non-applicable import lines.

4. PIPELINE.JSON (Phase B defaults, NO interview). If ROOT/.claude/pipeline.json is
   an older/minimal schema (missing the `diff-review` stage, or the
   memory/handoff/gates/orchestrator_gate blocks), REBUILD it from the source
   pipeline.json schema with defaults derived from the detected stack:
   - stages: [implement, test, review, diff-review, ready, done]
   - exit gates: implement -> build cmd, test -> test cmd, review -> lint cmd
     (empty string if the stack genuinely has none - common for infra/docs)
   - owners: implement -> senior-backend-dev (+ senior-frontend-dev if a frontend
     exists; devops-engineer for pure-infra projects), test -> qa-engineer,
     review -> reviewer + security-engineer
   - tools per stage: editing stages Read/Write/Edit/Bash/Glob/Grep; review
     read-only Read/Glob/Grep/Bash
   - branch {type}/{task}; protected main = detected main branch; 2 checkpoints
     (approve commit, approve push)
   - retry_budget 3; memory.enabled + handoff.enabled +
     handoff.document_latest_on_start = true
   - baseline (memory.baseline AND handoff.baseline) = highest done task id in
     tasks/done/ (grandfather prior work), or "" if none
   - gates: stack-appropriate destructive_command_patterns, repl_write_*,
     dev_forbidden_commands; forbid_dev_null true (Windows)
   - orchestrator_gate.enabled true
   Ensure NO `{{...}}` remain and the JSON is valid.

5. MEMORY MIGRATION. Memory is one store: .claude/memory/memory.db (see
   memory/README.md). Any .md file under .claude/memory/, any .claude/agent-memory/
   tree, and any docs/memory/ at the project root is OLD markdown memory that must
   be migrated into it. Run the built-in migrator first - it reads the legacy
   layouts and is idempotent:
   `python "ROOT/.claude/tools/memory/memory.py" --migrate`
   Then route by hand whatever it did not recognise, with
   `memory.py --record --kind <lesson|pattern|module>`:
   - module / where-things-live / architecture -> --kind module (path,
     responsibility, symbols, notes)
   - mistakes / caveats / gotchas / env-quirks -> --kind lesson (signature,
     trigger, what, why, fix; single-line fields, prose is refused)
   - reusable code patterns used more than once -> --kind pattern (name, use-when)
   - profile / project-overview prose -> fold into project/project-context.md
   Verify by count (`memory.py --stats`) BEFORE removing any markdown, then MOVE
   the migrated files into .claude/memory/legacy/ (create it; delete nothing).
   SECURITY: never copy secret VALUES (passwords, tokens, keys) into memory -
   record only the method / where-to-find, and note secrets were omitted.
   Respect the project .gitignore (some gitignore .claude/memory - still migrate
   on disk). If there is NO old memory, just record one module row per top-level
   module from project/architecture.md. Then stamp:
   `python "ROOT/.claude/tools/memory/codebase_sync.py" --stamp`

6. VERIFY (run; report each). Windows-safe, no unix redirects:
   - `grep -rE '\{\{[A-Z_]+\}\}' ROOT/.claude/` then ignore matches in
     _onboarding.md / _init-prompt.md / handoff-template.md -> should be empty
   - `grep -r 'PROJECT-SPECIFIC - REPLACE ME' ROOT/.claude/project/` -> empty
   - `python "ROOT/.claude/tools/pipeline/state.py" --show` -> loads without error

## Report (return this, max ~10 lines, plain text)

- state: already-onboarded | fresh
- stack: one-liner
- placeholders remaining after: N (must be 0)
- pipeline.json: kept | rebuilt (+ baseline used)
- memory: migrated N facts -> L1 x / L2 y / L3 z, originals moved to legacy;
  OR "none - seeds only"
- verify: check1 pass/fail, check2 pass/fail, engine loads yes/no
- manual-attention: anything left, or "none"

Do NOT commit anything.

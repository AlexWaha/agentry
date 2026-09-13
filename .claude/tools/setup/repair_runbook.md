# Fleet repair runbook (SEMANTIC ONLY - cheap, per project)

You finish onboarding for ONE project. You do ONLY the semantic parts; the
orchestrator runs the deterministic token substitution and pipeline rebuild
centrally afterward, so DO NOT run substitute_tokens.py, DO NOT hand-replace
tokens across many files, DO NOT edit pipeline.json. Keep it small.

You are given: ROOT = the project root (its .claude is at ROOT/.claude).

Hard rules: work only inside ROOT; do NOT touch git; ENGLISH only; NEVER use em
dash or en dash (only regular hyphen-minus); no AI attribution; on Windows never
use unix redirects (`2>/dev/null`); SOURCE template (read-only) is
d:/Work/AlexWaha.com/AI-team/AI-team-universal/.claude. The target's gate hooks
do not fire in this session.

Do exactly these three things:

1. STACK DETECTION + stack.md token table. Detect the real stack (manifests,
   lockfiles, Dockerfile/compose, CI, folder structure; *.local projects are
   usually OpenCart/PHP on OpenServer, not Docker). Open ROOT/.claude/project/stack.md
   and make its "Placeholder values for CLAUDE.md" table CORRECT and COMPLETE:
   - Every row MUST keep its token key in the left column in the exact form
     ``| `{{TOKEN}}` | value |`` (a prior interrupted run may have destroyed the
     left column by replacing `{{TOKEN}}` with its value - if so, REBUILD the
     table with the proper `{{TOKEN}}` keys).
   - Include a row with a REAL value for every one of these tokens (use the
     detected value, or `none`/`n/a` where it genuinely does not apply - never
     leave a bracket-marker or a bare `{{...}}` in a value cell):
     PROJECT_NAME, PRODUCT_DESCRIPTION, STACK, LANG, FRAMEWORK, TEST_CMD,
     FORMAT_CMD, LINT_CMD, BUILD_CMD, DEV_CMD, MAIN_BRANCH, DEFAULT_DB,
     AUTH_METHOD, BACKEND_STACK, TEST_FRAMEWORK, FORMATTER, CONTAINER_TOOL,
     CI_TOOL, PKG_MANAGER, CACHE_BACKEND, BACKEND_RUNTIME, FRONTEND_RUNTIME,
     FRONTEND_STACK, FRONTEND_TEST_CMD, BACKEND_TEST_CMD, MIGRATION_TOOL,
     WEB_SERVER, TYPECHECK_CMD, PROJECT_DB_NAME, PROJECT_DB_USER,
     EXTERNAL_SERVICE, FRAMEWORK_DOCS_URL, COMM_LANG (= English), SRC_DIR,
     RESOURCE, MODULE_A, MODULE_B, MODULE_C.
   - PROJECT_DB_* / secrets: record the NON-secret name only, never a password.
   This table is the machine map the orchestrator's substituter reads - its
   correctness is the whole job. Do NOT substitute these tokens yourself.

2. project/*.md markers. In project-context.md, architecture.md,
   api-conventions.md, replace any remaining `[PROJECT-SPECIFIC - REPLACE ME]`
   marker and any leftover foreign-framework boilerplate with correct content for
   THIS project's real stack. Leave project/README.md as-is. If a file is already
   real and marker-free, do not touch it.

3. MEMORY (only if not already migrated). If ROOT/.claude/memory/legacy/ exists,
   memory was already migrated - SKIP. Otherwise: memory is one store,
   ROOT/.claude/memory/memory.db (see memory/README.md). Any .md under
   ROOT/.claude/memory/, any ROOT/.claude/agent-memory/ tree and any
   ROOT/docs/memory/ are OLD markdown memory. Run the migrator (idempotent):
   `python "ROOT/.claude/tools/memory/memory.py" --migrate`
   Route what it did not recognise with `memory.py --record --kind
   <lesson|pattern|module>`: module facts -> module rows; caveats/gotchas ->
   lesson rows (signature, trigger, what, why, fix); reusable patterns -> pattern
   rows; profile/overview -> project-context.md. Never copy secret values. Verify
   by count (`memory.py --stats`), then MOVE the migrated files into
   ROOT/.claude/memory/legacy/ (create it; delete nothing). If there is no old
   memory, just record one module row per top-level module from architecture.md.
   Then run:
   `python "ROOT/.claude/tools/memory/codebase_sync.py" --stamp`

Return a SHORT report (max ~8 lines): stack one-liner; stack.md table
complete yes/no (rows filled); project/*.md markers cleared yes/no; memory
migrated N facts -> layers / or "none - seeds only" / or "already-migrated".
Do NOT run substitute_tokens.py, do NOT edit pipeline.json, do NOT commit.

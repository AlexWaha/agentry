# Fleet task: populate the stack.md token MAP only (very small job)

You do ONE narrow thing for ONE project so the orchestrator's deterministic
substituter can fill the remaining {{TOKEN}} placeholders. Do NOT run
substitute_tokens.py, do NOT edit any file other than project/stack.md, do NOT
touch pipeline.json, memory, git, or project/*.md. English only; no em/en dash
(regular hyphen only).

You are given ROOT. Target file: ROOT/.agentry/project/stack.md

The remaining {{TOKEN}} placeholders across this project's agents/rules failed to
substitute because the "Placeholder values for CLAUDE.md" table in stack.md is
missing or lost its `{{TOKEN}}` left-column keys. Fix ONLY that table:

1. Detect the real stack from the repo (manifests, lockfiles, Dockerfile/compose,
   CI, folder structure) - enough to know the commands, DB, runtime, framework.
2. In ROOT/.agentry/project/stack.md, ensure there is a section titled exactly
   "## Placeholder values for CLAUDE.md" containing a markdown table whose every
   row has this EXACT shape (backticks around the braced token in the LEFT cell):

       | Placeholder | Value |
       |-------------|-------|
       | `{{PROJECT_NAME}}` | Acme Thing |
       | `{{TEST_CMD}}` | pytest |
       ...

   Include a row with a REAL value for EVERY token below. Use `none` or `n/a`
   where the token genuinely does not apply to this stack (never a bracket-marker,
   never a bare `{{...}}` in the value cell, never a secret value):
     PROJECT_NAME, PRODUCT_DESCRIPTION, STACK, LANG, FRAMEWORK, TEST_CMD,
     FORMAT_CMD, LINT_CMD, BUILD_CMD, DEV_CMD, MAIN_BRANCH, DEFAULT_DB,
     AUTH_METHOD, BACKEND_STACK, TEST_FRAMEWORK, FORMATTER, CONTAINER_TOOL,
     CI_TOOL, PKG_MANAGER, CACHE_BACKEND, BACKEND_RUNTIME, FRONTEND_RUNTIME,
     FRONTEND_STACK, FRONTEND_TEST_CMD, BACKEND_TEST_CMD, MIGRATION_TOOL,
     WEB_SERVER, TYPECHECK_CMD, PROJECT_DB_NAME, PROJECT_DB_USER,
     EXTERNAL_SERVICE, FRAMEWORK_DOCS_URL, COMM_LANG (= English), SRC_DIR,
     RESOURCE, MODULE_A, MODULE_B, MODULE_C.
   If a "Placeholder values" table already exists but has bare keys (left column
   shows values instead of `{{TOKEN}}`), rewrite the left column back to the
   `{{TOKEN}}` keys. If some rows exist and are correct, keep them and add the
   missing ones.
3. Self-check: run `grep -c '{{' ROOT/.agentry/project/stack.md` (Windows-safe, no
   redirects) and confirm it returns a number >= 38. If not, fix the table.

Return ONE short line: "<project>: table has N token rows, grep {{ count = M".
Nothing else. Do not edit anything but stack.md.

---
name: write-tests
description: Generate comprehensive tests covering authentication, authorization, validation, happy paths, and not-found for an API endpoint or service. Use when a task asks to "add tests" or "write tests for <endpoint/service>", when a PR is missing test coverage, when starting from a spec with no existing code (TDD mode), or as the final step of feature-scaffold/bug-fix.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Write Tests

Generate tests for existing or new code so every endpoint is covered across
five required categories: authentication, authorization, validation, happy
path, and not-found. Concrete framework and runner command
(`{{TEST_FRAMEWORK}}`, `{{TEST_CMD}}`) live in `.claude/project/stack.md`.

## Steps

1. **Read the implementation chain.** Route (identifier, HTTP method,
   middleware), Controller (methods, DI, response shape), Validator/Request
   (`authorize()` logic, rules), Service (business logic, edge cases),
   Model/Entity (relationships, scopes, casts, fillable), API Resource/DTO
   (response structure), Factory/Builder (available states).
2. **Determine test type and location.** API endpoint (controller +
   validator + resource) -> feature/integration test; service logic -> unit
   test; model scopes/methods -> unit test; background job/worker -> feature
   test. Exact layout is in `project/architecture.md`.
3. **Cover the five required categories for every endpoint** - non
   negotiable: authentication (401, unauthenticated rejected), authorization
   (403, wrong user/role/plan rejected), validation (422, one case per
   validator rule), happy path (200/201, response shape AND persisted
   state), not found (404, non-existent resource). Plus recommended extras:
   empty collection, pagination, filtering, ownership isolation.
4. **Follow AAA + DAMP.** Arrange / Act / Assert per test. Prefer DAMP
   (Descriptive And Meaningful Phrases) over DRY - duplicated setup is fine
   if each test stays self-contained and readable without scrolling.
5. **Use an authenticated-user helper.** Every framework has a way to create
   an authenticated caller (factory + token/session/API key per
   `{{AUTH_METHOD}}`); write one helper and reuse it in every test that
   needs auth.
6. **Write the endpoint test file**, covering every category from step 3 for
   each HTTP verb the resource exposes. Full skeleton plus concrete
   Pest/Vitest/pytest snippets: `references/test-examples.md`.
7. **Write service unit tests** for business logic without the HTTP layer
   (list/filter/paginate, create in a transaction with rollback on failure,
   delete). Pseudocode: `references/test-examples.md`.
8. **Run the tests.** `{{TEST_CMD}}` (full suite), `{{TEST_CMD}} <path>`
   (targeted), `{{TEST_CMD}} --filter "<pattern>"` (by name). Diagnose
   before "fixing": 401 instead of expected -> missing auth middleware or
   token not sent; unexpected 422 -> validator rules mismatch test data;
   500 -> missing migration/relationship/service bug; wrong JSON structure
   -> resource/DTO mismatch; count mismatch -> factory creating extra
   related records.
9. **Apply conventions.** Named status helpers or framework constants only,
   never numeric literals (`200`, `422`). One concept per test. Descriptive
   names ("returns 403 when accessing another user's resource", not "it
   works"). Self-contained tests. No logic in assertions (no
   `filter()`/`map()`/`sum()`/ternaries) - explicit expected values.
   Datasets/table tests for validation rules, one row per rule. Test
   persisted state over mock call counts. Test through the public API only,
   never private methods.

## TDD mode

Use instead of the default flow above when the task starts from a spec or
requirements document with no existing implementation (e.g. "implement X per
this spec", a greenfield service or endpoint).

1. **RED.** Write a failing test that encodes the desired behavior straight
   from the spec, before any implementation exists. Run it and confirm it
   fails for the right reason (missing behavior, not a typo).
2. **GREEN.** Write the minimal code needed to make that test pass - nothing
   beyond what the test requires.
3. **REFACTOR.** Clean up implementation and test code, re-running the suite
   after each change; it must stay green throughout.

Repeat the loop per behavior/spec item, then apply steps 3 and 9 above (five
required categories, conventions) to fill any remaining coverage gaps.

## Output checklist

- [ ] Implementation chain read (route -> controller -> validator -> service
      -> model -> resource -> factory)
- [ ] All 5 required categories covered (401, 403, 422, 2xx, 404) plus
      ownership isolation
- [ ] Validation datasets cover every rule in the validator
- [ ] Happy-path tests verify response shape AND persisted state
- [ ] Edge cases covered (empty collection, pagination, boundary values)
- [ ] All tests pass; no numeric HTTP status literals
- [ ] Formatter + linter clean on test files

## References (read only when needed)

- [references/test-examples.md](references/test-examples.md) - endpoint test
  skeleton, concrete Pest/Vitest/pytest examples, service unit test
  pseudocode, required scenarios table
- [references/qa-examples.md](references/qa-examples.md) - five mandatory
  category examples across multiple frameworks plus e2e tests

---
name: feature-scaffold
description: Scaffold a complete API feature end-to-end - migration, model, factory, validators, service, controller, resource/DTO, routes, tests. Use when implementing a new resource/endpoint/CRUD feature, when a task says "create/add <entity> API", or when extending a module with a new persisted entity.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Feature Scaffold

Produce every artifact of a complete API feature following the project's layering
pipeline: `Request -> Validator -> Service -> Resource/DTO -> Response`.
Concrete CLI commands live in `.claude/project/stack.md`; the module/folder
layout in `.claude/project/architecture.md`.

## Input

Gather before starting: resource name (singular, PascalCase), table name
(plural, snake_case), attributes (types, nullable, defaults), relationships,
auth requirement (`{{AUTH_METHOD}}`), business rules beyond CRUD.

## Steps

1. **Check sibling files for conventions.** Glob + Read existing models,
   controllers, validators, resources, services, factories, migrations, tests
   of the same kind. Match their naming, structure, and style. Run the
   Pre-Flight Checks from `rules/quality-standard.md` - the artifact may
   already exist.
2. **Migration.** Generate via the framework CLI (`{{MIGRATION_TOOL}}`), never
   hand-write the filename. Schema-only, reversible, index every column used in
   WHERE/JOIN/ORDER BY, explicit `ON DELETE` behavior on foreign keys. Details:
   `migration` skill.
3. **Model / Entity.** Data container only: fillable fields, casts,
   relationships, scopes. No business logic.
4. **Factory / test data builder.** Realistic defaults plus at least one named
   state (e.g. `inactive`).
5. **Store validator.** Field rules AND `authorize()` (authentication check).
6. **Update validator.** Partial rules ("sometimes"); `authorize()` enforces
   ownership.
7. **Service / use case.** All business logic; one method per operation;
   transactions around multi-step writes. Depends on models/services, never on
   controllers or resources.
8. **Controller / handler.** Thin: validated input -> service -> resource/DTO.
   Status codes via framework constants, never numeric literals.
9. **API Resource / DTO.** Public contract only, conditional relationship
   loading, stable versioned shape. Computed fields live here.
10. **Routes.** Versioned prefix (`/api/v1/...`), named, behind the
    `{{AUTH_METHOD}}` middleware/guard.
11. **Tests.** All 5 categories per endpoint - 401, 403, 422, 200/201, 404 -
    plus edge cases. Details: `write-tests` skill.
12. **Quality gate.** `{{FORMAT_CMD}}` -> `{{LINT_CMD}}` -> `{{TEST_CMD}}` ->
    grep the diff for forbidden debug patterns. Details: `health-check` skill.

## Output checklist

- [ ] Migration generated via CLI, runs, is reversible, indexes in place
- [ ] Model has fillable, casts, relationships, scopes - no business logic
- [ ] Factory has realistic defaults and at least one state
- [ ] Store validator: `authorize()` + complete field rules
- [ ] Update validator: ownership check + partial rules
- [ ] Service holds all business logic (list / create / update / delete)
- [ ] Controller is thin; resource/DTO shapes the response
- [ ] Routes versioned, named, auth-guarded
- [ ] Tests cover 401 / 403 / 422 / 2xx / 404 + edge cases
- [ ] Formatter, linter, tests all pass; no forbidden patterns in the diff

## References (read only when needed)

- [references/backend-layering.md](references/backend-layering.md) - per-layer
  code examples (Laravel, Prisma, Zod, TypeScript, Express, Pest) and layer
  responsibility rules
- [references/frontend-patterns.md](references/frontend-patterns.md) - web and
  mobile component patterns, shared state, API client, component tests

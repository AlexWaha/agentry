---
name: migration
description: Create safe, reversible database migrations with proper column types, indexes, foreign keys, and cascade updates to model/factory/resource. Use when a task adds/renames/drops a column or table, adds an index or foreign key, or when a feature-scaffold needs its schema step done in isolation.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Migration

Create database migrations safely: never edit an existing migration, always
create a new one, and keep every schema change reversible. Concrete migration
tool (`{{MIGRATION_TOOL}}`) and database engine (`{{DEFAULT_DB}}`) live in
`.agentry/project/stack.md`.

## Steps

1. **Classify the change.** New table, add column(s), add index, add foreign
   key, add unique constraint, modify column, rename, or destructive drop.
   Rename and destructive drops need explicit CEO approval before proceeding -
   see the classification table in `references/migration-examples.md`.
2. **Check existing migration state.** Run `<migration tool> status` to see
   applied/pending migrations. Glob `**/migrations/*_create_<table>_table.*`
   and `**/migrations/*_<table>_*.*` so you don't duplicate columns or indexes
   already defined for the target table.
3. **Generate the migration file via the framework CLI.** Never hand-create
   the filename. Name it per convention: `create_<table>_table`,
   `add_<col>_to_<table>_table`, `add_index_to_<table>_table`,
   `add_<ref>_fk_to_<table>_table`, `modify_<col>_in_<table>_table`,
   `rename_<old>_to_<new>_in_<table>_table`, or
   `remove_<col>_from_<table>_table` (approval required).
4. **Write `up` and `down`.** Schema-only - no data manipulation, no business
   logic backfills; large data changes go through a separate data-migration
   mechanism. `down` must exactly reverse `up`. New columns on existing tables
   must be nullable or have a default so the migration is zero-downtime safe.
   Index every column used in `WHERE`, `JOIN`, or `ORDER BY`. Every foreign key
   has an explicit `ON DELETE` behavior: `CASCADE`, `SET NULL`, or `RESTRICT`.
   Code patterns for each case: `references/migration-examples.md`.
5. **Pick the smallest column type that fits.** See the type guideline table
   in `references/migration-examples.md` (never use `float` for money, always
   `decimal(p, s)`).
6. **Update related artifacts.** Model/entity (fillable/permitted list, casts,
   relationships), factory/builder (default + new states), API resource/DTO
   (expose new field if public), validator (field rules), and any generated
   type definitions (TypeScript interfaces, OpenAPI spec). Don't leave these
   out of sync with the schema.
7. **Run the migration.** `<migration tool> migrate`. If it fails, read the
   error, fix the file, and re-run; roll back first (`<migration tool>
   rollback`) if the schema was partially applied.
8. **Verify.** `<migration tool> status` confirms the new migration shows as
   applied.
9. **Document deploy actions.** Every migration lands in the task report's
   Deploy Actions section - format in `references/migration-examples.md` and
   `rules/git-workflow.md`.

## Output checklist

- [ ] Migration file generated via CLI (not hand-created)
- [ ] `up` has all columns with proper types, nullability, defaults
- [ ] `down` reverses `up` exactly
- [ ] Indexes added for every column used in `WHERE`, `JOIN`, `ORDER BY`
- [ ] Foreign keys have explicit `ON DELETE` behavior
- [ ] New columns on existing tables are nullable or have a default
- [ ] Schema-only - no data manipulation mixed in
- [ ] Model/entity, factory/builder, API resource/DTO, validator updated
- [ ] Migration ran successfully; `status` confirms it applied
- [ ] Deploy actions documented in the task report

## References (read only when needed)

- [references/migration-examples.md](references/migration-examples.md) -
  create-table / add-column / add-index / add-foreign-key / add-unique-constraint
  patterns (Laravel, TypeORM, Alembic examples), column type guideline table,
  change classification table, and the Deploy Actions format

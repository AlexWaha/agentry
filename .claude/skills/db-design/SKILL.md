---
name: db-design
description: Design a complete database schema - entities, relationships, indexes, ER diagram, migration plan, and soft delete strategy - from product requirements and API contracts. Use when a task says design the database/schema, add new tables or columns, plan migrations for a new feature, needs an ER diagram, or when API design surfaces fields that have no backing table yet.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Database Design

Design a complete database schema from product requirements and API
contracts: entity definitions, relationships, indexes, an ER diagram, and a
migration execution plan. Concrete migration CLI commands and column type
conventions live in `.agentry/project/stack.md`.

## Steps

1. **Read inputs.** `docs/product/product-spec.md`, `docs/technical/api-contracts.md`
   (if it exists), `docs/technical/architecture.md` (if it exists). If API
   contracts exist, the schema must support every request/response field
   defined there; otherwise derive entities from the product spec.
2. **Identify all entities and attributes.** For each entity: table name
   (plural, snake_case), all columns with types, nullability, defaults,
   whether it uses soft deletes, whether it uses UUIDs or auto-increment IDs.
   Table shape example: `references/schema-examples.md`.
3. **Define relationships.** Map every relationship: parent, child, type
   (1:1, 1:N, N:N), FK column, `ON DELETE` behavior, and a one-line reason.
   List pivot tables separately with their extra columns.
4. **Design indexes.** Every foreign key, unique constraint, and column used
   in frequent WHERE/JOIN/ORDER BY needs an index; add composite indexes for
   common multi-column query patterns.
5. **Create the ER diagram in Mermaid.** Show all entities, columns, and
   relationships (`erDiagram`). Validate it renders via the Mermaid MCP tool.
6. **Plan the migration sequence.** Order tables by FK dependency (no table
   can reference one that does not exist yet). Use the framework's timestamp
   naming convention for migration files.
7. **Define the soft delete strategy.** Which tables soft-delete and why
   (recovery, GDPR); which cascade with a hard-deleted parent vs stay
   orphaned but inaccessible; hard deletes only via explicit admin action.
8. **Write migration code for each table.** Use the framework's schema
   builder syntax (columns, foreign keys, indexes) so migrations can be
   generated directly from this plan.
9. **Write the output document.** `docs/technical/db-schema.md`: entity
   inventory, relationship map, ER diagram, index definitions, migration
   plan, soft delete strategy, migration code examples.

## Output checklist

- [ ] All entities identified with complete column definitions
- [ ] All relationships mapped with FK constraints and cascade behavior
- [ ] ER diagram created and validated in Mermaid
- [ ] Indexes defined for all FK, unique, and frequent query columns
- [ ] Migration sequence ordered by FK dependencies
- [ ] Soft delete strategy documented
- [ ] Migration code uses the project's schema builder conventions
- [ ] Output written to `docs/technical/db-schema.md`

## References (read only when needed)

- [references/schema-examples.md](references/schema-examples.md) - entity
  table examples, relationship map, index definitions, Mermaid ER diagram,
  migration ordering, soft delete strategy, and a schema builder code example
- [references/sql-examples.md](references/sql-examples.md) - pipeline
  engineering examples (Spark/Delta Lake ingestion, dbt contracts, Great
  Expectations validation, Kafka streaming) for bronze/silver/gold data flows

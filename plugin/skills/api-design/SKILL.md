---
name: api-design
description: Design RESTful API endpoints, request/response schemas, error formats, and generate an OpenAPI 3.0 spec for a feature or the whole application. Use when a task says design/spec the API, add new endpoints, define request or response schemas, needs an OpenAPI/Swagger spec, plans resource routes and naming, or defines pagination/error envelope/versioning conventions before implementation.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# API Design

Design complete API contracts for a feature or the entire application: endpoint
definitions, request/response schemas, error format, pagination, and an OpenAPI
spec. Concrete framework validation syntax and route conventions live in
`.agentry/project/stack.md`.

## Steps

1. **Read inputs.** Read `docs/product/product-spec.md`, `docs/technical/architecture.md`
   (if it exists), and `docs/business/product-vision.md` (if it exists). Identify
   scope: full API design or a single feature addition.
2. **Identify resources (nouns).** Extract every domain entity needing API
   representation. For each: name (singular/plural), type (primary, sub-resource,
   action endpoint), owner, access level. Build a resource inventory table.
   Example shapes: `references/api-examples.md`.
3. **Define RESTful endpoints per resource.** Standard CRUD set plus any
   non-CRUD action endpoints. Naming rules: plural nouns for collections
   (`/calendars`), singular verbs for actions on a resource
   (`/calendars/{id}/duplicate`), nested resources max 2 levels deep, query
   parameters for filtering/sorting/pagination never new verbs in the path.
4. **Design request schemas.** For every POST/PATCH/PUT endpoint, define the
   request body with field-level validation rules (type, required/nullable,
   constraints, enum values). Use the project's validation rule format
   (see `references/api-examples.md`).
5. **Design response schemas.** Follow the framework's resource/DTO
   conventions: wrap payloads in a `data` key, use conditional loading for
   optional relationships, expose computed fields explicitly, ISO 8601 for
   dates, stable public IDs (UUIDs where used).
6. **Define the error response format.** One consistent shape reused across
   every endpoint for 401, 403, 404, 422, 429, and 500. Validation errors
   (422) must list per-field messages.
7. **Define pagination and filtering.** Standard query parameters (`page`,
   `per_page` with a max), plus per-resource filter/sort parameters
   (`search`, `sort_by`, `sort_order`, date range filters as applicable).
8. **Generate the OpenAPI 3.0 spec.** Full YAML: `info`, `servers`,
   `security`, `paths` for every endpoint, and reusable `components`
   (`schemas`, `responses`, `securitySchemes`). Keep request/response schemas
   in components, referenced via `$ref`, not duplicated inline.
9. **Diagram complex flows.** For non-trivial multi-step flows (imports,
   batch processing, async jobs), add a Mermaid sequence diagram; validate it
   renders via the Mermaid MCP tool.
10. **Write output docs.** `docs/technical/api-contracts.md` (resource
    inventory, endpoints, request/response schemas, error format, pagination,
    sequence diagrams) and `docs/api/openapi.yaml` (the full spec).

## Output checklist

- [ ] All domain resources identified and documented
- [ ] Every endpoint has method, path, description, auth level
- [ ] Request schemas include validation rules
- [ ] Response schemas match the project's resource/DTO conventions
- [ ] Error format is consistent across all endpoints
- [ ] Pagination and filter parameters documented
- [ ] OpenAPI spec is valid YAML
- [ ] Complex flows have sequence diagrams
- [ ] Both output files written to correct locations

## References (read only when needed)

- [references/api-examples.md](references/api-examples.md) - resource
  inventory example, CRUD/action endpoint tables, request/response JSON
  examples, error format examples, pagination parameter tables, a full
  OpenAPI 3.0 YAML sample, and a Mermaid sequence diagram example

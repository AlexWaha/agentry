# API Conventions

> **Project-specific API conventions live in `.claude/project/api-conventions.md`.**
> That file describes framework-specific controller/validator/resource patterns for the current project. Read it alongside this one.

This file covers the universal REST principles that apply regardless of stack.

---

## Versioning

All API endpoints must be versioned: `/api/v1/…`, `/api/v2/…`, etc. Never ship an unversioned public API.

## Route Naming

All routes must have stable, named identifiers (not just URL strings). Use named-route helpers for URL generation in code; never hardcode paths.

## Thin Controllers / Handlers

The controller / route handler is responsible for three things only:

1. Accept the validated request
2. Delegate work to a service / use case
3. Return a formatted response (resource / DTO)

**Never** put business logic, data manipulation, or side effects (notifications, external API calls) in a controller.

## Validation

Validate every incoming request through a dedicated validator class (FormRequest, schema, DTO validator). Never use inline validation inside a controller.

Validators own both:
- Field-level rules (types, required, formats)
- Authorization (is this caller allowed to do this?)

## Responses

Always wrap responses in API resources / DTOs. Never return raw entity / model instances from an API.

- Include only the fields that belong in the public contract
- Use conditional loading for relationships (don't ship lazy loads)
- Keep response shape stable per API version

## Pagination

List endpoints use pagination. Default page size should be a reasonable small number (e.g., 20-30). Expose `per_page` as a request parameter with a hard cap.

For small, bounded collections (enum values, categories, timezones) a full list is acceptable - but document the upper bound.

## Conditional Filtering

Build list queries from optional filters declaratively (e.g., `when()` chains), not with `if`/`else` branches. Every filter is a one-liner that adds a constraint only when the filter is present.

## HTTP Status Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST that creates a resource |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Malformed request (rare - usually 422) |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Authenticated but lacking permission |
| 404 | Not Found | Resource does not exist |
| 409 | Conflict | State conflict (e.g., unique violation at business level) |
| 422 | Unprocessable Entity | Validation errors |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Unhandled exception |

Never use magic numbers in code - use framework constants (`Response::HTTP_*`, `StatusCode.CREATED`, etc.).

## Error Format

Errors must follow a consistent structure across the API. At minimum include:

- `message` - human-readable summary
- `errors` - field-level validation errors (when applicable, 422 responses)

Never leak stack traces, SQL, or internal class names to API consumers.

## Route Model Binding / Resource Resolution

Where the framework supports it, resolve resources by ID (or slug) implicitly at the route layer. Centralize "not found" handling there - don't repeat `findOrFail` checks inside every handler.

## What NOT to Do

- No raw entity/model returns - always wrap in resources/DTOs
- No inline validation in handlers - always use dedicated validator classes
- No business logic in controllers - delegate to services
- No hardcoded URLs - use named-route helpers
- No raw SQL/DB facade use in controllers - use the ORM / query builder at the service layer
- No magic numbers for HTTP status codes - use framework constants

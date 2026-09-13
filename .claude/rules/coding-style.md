# Coding Style

Universal coding conventions for all languages in the project. Every developer (human or AI) follows these rules without exception.

Framework-specific code patterns (full controller / validator / resource / model examples for the current stack) live in `.agentry/project/api-conventions.md` and `.agentry/project/architecture.md`. This file covers the stack-agnostic principles.

---

## Backend Conventions (Stack-Agnostic)

### Controllers / Route Handlers

Controllers must be **thin**. Their only job: accept a validated request, call a service, return a formatted response. No business logic, no data transformation, no side effects.

Responsibilities of a controller / route handler:

1. Receive the validated input (via a dedicated validator class, schema, or DTO)
2. Delegate work to one or more services
3. Return a formatted response (resource / DTO / serializer output)

Forbidden in controllers:

- Inline validation of raw input
- Direct database queries or ORM calls that encode business rules
- Side effects: emails, notifications, external API calls, file I/O
- Transformation of entities into response shape (that is the resource/DTO's job)
- Conditional branching that encodes business rules

> `[EXAMPLE - PHP/Laravel]` and `[EXAMPLE - TypeScript/Express]` snippets for the current stack live in `project/api-conventions.md`.

Generic pseudocode:

```
function handler(validatedInput):
    result = service.performAction(currentUser, validatedInput)
    return ResourceOrDTO.from(result).withStatus(CREATED_OR_OK)
```

### Services / Use Cases

All business logic lives in service / use-case classes. Services accept validated data, perform operations, return domain objects or results.

Rules:

- One service method = one business operation (create, update, duplicate, cancel, etc.)
- Wrap multi-step database writes in a transaction
- Services may depend on other services, repositories, domain models, and events
- Services must NOT depend on controllers, validators, or resources/DTOs
- If a service exceeds ~300 lines, split it by responsibility

Generic pseudocode:

```
class ResourceService:
    function create(user, data):
        begin_transaction()
        resource = Resource.new(data)
        resource.owner = user
        resource.save()
        dispatch_event(ResourceCreated(resource))
        commit_transaction()
        return resource
```

### Models / Entities

Models are data containers with relationships, scopes, and value-object conversions. Business logic does NOT live in models.

Required conventions (adapt to the stack):

- Explicit declaration of mass-assignable fields / allowed setters
- Explicit relationship definitions with return type hints where the language supports them
- Query scopes / query-builder extensions for common filters
- Soft deletes by default on user-generated content
- Factories (or equivalent test data builders) for every model
- No business logic - models are data + relationships + scopes only

### Enums / Constants

Use typed enums (or the closest equivalent in the language) for all status and type fields. TitleCase case names. Back enums with primitive values (string or int) for persistence.

Add methods on the enum for behavior tied to the enum value (display label, default configuration, business rules that depend only on the enum state).

Generic pseudocode:

```
enum ResourceType : string {
    case Daily   = 'daily'
    case Weekly  = 'weekly'
    case Monthly = 'monthly'

    function label() -> string:
        match self:
            Daily   => 'Daily'
            Weekly  => 'Weekly'
            Monthly => 'Monthly'
}
```

### Validation (FormRequests / Schemas / DTOs)

Validate every incoming request through a dedicated validator class. Never use inline validation in controllers.

The validator owns two responsibilities:

1. **Field rules** - types, required flags, formats, enum membership, ranges
2. **Authorization** - is this caller allowed to perform this action on this resource

Use enum-aware validation where possible (match against enum cases, not raw strings).

### API Resources / DTOs

All API responses must pass through a dedicated resource / DTO / serializer. Never return raw entities or models from an API.

Rules:

- Include only fields that belong in the public contract
- Load relationships conditionally (skip lazy-load pitfalls)
- Keep response shape stable per API version
- Computed fields (e.g., completion rates, derived counts) go in the resource, not in the model

### Cross-Module Communication

Module structure and module dependency rules are stack-specific. See `.agentry/project/architecture.md` for the project's module layout and cross-module communication patterns (typically: shared interfaces in a core module, no direct cross-module entity imports, shared enums/DTOs in the core module).

### Language-Specific General Conventions

**PHP**
- `declare(strict_types=1);` at the top of every file
- Constructor property promotion
- Explicit return type declarations on all methods and functions
- `final class` by default for services unless inheritance is needed
- Named arguments when calling methods with many parameters
- Curly braces for all control structures, even single-line bodies

**TypeScript**
- `strict: true` in `tsconfig.json`
- Explicit return types on exported functions
- No `any` - use `unknown` and narrow with type guards
- Prefer `interface` for object shapes; `type` for unions / intersections

**Python**
- Type hints on all function signatures (parameters and return type)
- Use dataclasses or Pydantic models for structured data
- No mutable default arguments

**Go**
- Error handling: check every error, never ignore with `_`
- Use struct tags for serialization, not ad-hoc maps

**Ruby**
- Use `frozen_string_literal: true` at the top of every file
- Explicit method visibility (`private`, `protected`)

---

## React / TypeScript Conventions

### Functional Components Only

No class components. Ever.

```tsx
interface ResourceCardProps {
  resource: Resource;
  onSelect: (id: string) => void;
  isSelected?: boolean;
}

export function ResourceCard({ resource, onSelect, isSelected = false }: ResourceCardProps) {
  const progress = useProgress(resource.id);

  return (
    <div
      className={cn('resource-card', { 'resource-card--selected': isSelected })}
      onClick={() => onSelect(resource.id)}
      role="button"
      tabIndex={0}
    >
      <h3>{resource.name}</h3>
      <span className="resource-card__type">{resource.typeLabel}</span>
      <ProgressBar value={progress} />
    </div>
  );
}
```

### Hooks for All State and Side Effects

```tsx
export function useResources(filters?: ResourceFilters) {
  const [items, setItems] = useState<Resource[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchItems = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.resources.list(filters);
      setItems(response.data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch'));
    } finally {
      setIsLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  return { items, isLoading, error, refetch: fetchItems };
}
```

### TypeScript Strict Mode

- `strict: true` in `tsconfig.json`
- All props must have explicit interfaces (not inline types for complex shapes)
- No `any` type - use `unknown` and narrow with type guards
- Prefer `interface` over `type` for object shapes (interfaces are extendable)
- Use discriminated unions for complex state

```tsx
interface LoadingState { status: 'loading'; }
interface SuccessState<T> { status: 'success'; data: T; }
interface ErrorState { status: 'error'; error: Error; }

type AsyncState<T> = LoadingState | SuccessState<T> | ErrorState;
```

### File and Folder Structure

```
src/
├── components/          # Reusable UI components
│   ├── ResourceCard/
│   │   ├── ResourceCard.tsx
│   │   ├── ResourceCard.test.tsx
│   │   └── index.ts
│   └── ui/              # Generic UI primitives (Button, Input, Modal)
├── hooks/               # Custom hooks
├── pages/               # Page-level components (route targets)
├── services/            # API client, external service wrappers
├── stores/              # State management
├── types/               # Shared TypeScript interfaces and types
├── utils/               # Pure utility functions
└── App.tsx
```

### Select Components

When a `<Select>` has many options (timezones, countries, currencies, etc.), always add a search/filter input inside `<SelectContent>` so the user can type to narrow results. Never force users to scroll through hundreds of items.

### Naming Conventions

- Components: `PascalCase` (`ResourceCard.tsx`)
- Hooks: `camelCase` with `use` prefix (`useResources.ts`)
- Utils/helpers: `camelCase` (`formatDate.ts`)
- Types/interfaces: `PascalCase` (`ResourceFilters`)
- Constants: `SCREAMING_SNAKE_CASE` (`MAX_ITEMS_FREE_TIER`)
- CSS classes: `kebab-case` or BEM (`resource-card__title--active`)

---

## React Native Conventions

Follow all React/TypeScript conventions above, plus:

- Share business logic (hooks, API client, types, utils) in a shared package
- Platform-specific UI components are separate
- Use `Platform.select()` or `.ios.tsx` / `.android.tsx` for platform differences
- Always test on both iOS and Android simulators
- See `rules/mobile.md` for detailed mobile-specific rules

---

## Forbidden Patterns (Per Language)

### PHP
- Debug functions (never commit): `dd()`, `dump()`, `ray()`, `var_dump()`, `print_r()`
- OS command execution: `exec()`, `shell_exec()`, `system()`, `passthru()`, `proc_open()`
- Code execution: `eval()`
- `env()` outside config files - always use `config('key')`
- Raw DB facade for standard operations - use ORM/query builder
- Inline validation in controllers - always use dedicated validator classes
- Raw model returns from API - always use resources/DTOs
- Business logic in controllers or models

### JavaScript / TypeScript
- Debug output in committed code: `console.log()`, `console.debug()` (use a proper logger)
- `debugger;` statements
- `any` type - use `unknown` and type guards
- `var` - use `const` or `let`
- `eval()`, `new Function()`, `setTimeout('string', ...)`
- Inline styles (use CSS/Tailwind classes)
- `document.querySelector()` in React components - use refs

### Python
- Debug output in committed code: `print()`, `pprint.pprint()`
- `breakpoint()`, `pdb.set_trace()`, `ipdb.set_trace()`
- `eval()`, `exec()`
- `os.system()`, unsanitized `subprocess.run(shell=True)`
- Bare `except:` - always catch specific exceptions

### Go
- Debug output in committed code: `fmt.Println`, `fmt.Printf`, `log.Println` outside proper logging
- Ignoring errors with `_` (always check or wrap)
- `panic()` outside of `main()` initialization

### Ruby
- Debug output in committed code: `puts`, `p`, `pp`, `print`
- `binding.pry`, `debugger`, `byebug`
- `eval`, `instance_eval` on untrusted input
- Backtick shell execution on untrusted input

### All Languages
- Commented-out code blocks - delete it, git has history
- TODO/FIXME without a linked task number
- Magic numbers without named constants
- Hardcoded secrets, API keys, or credentials
- PII in log statements

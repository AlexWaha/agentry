# Testing

Universal testing rules covering backend API endpoints, frontend components, and end-to-end flows. These rules apply regardless of language or test framework. Framework-specific examples below are marked with `[EXAMPLE - <stack>]` markers - adapt to the project's chosen test runner.

Common equivalents:

| Concern | PHP | JavaScript / TypeScript | Python | Go | Ruby |
|---|---|---|---|---|---|
| Test runner | Pest / PHPUnit | Vitest / Jest | pytest | `go test` + testify | RSpec |
| HTTP client | Laravel test helpers | supertest / MSW / fetch | httpx / requests | `net/http/httptest` | Rack::Test |
| Mocking | Mockery | vi.fn / jest.fn | unittest.mock | gomock | RSpec mocks |
| Factories / fixtures | Model factories | fishery / factory-bot-ts | factory-boy | testfixtures | factory_bot |

---

## Backend Testing

### Framework and Configuration

Use whatever the project's `project/stack.md` specifies. Core expectations regardless of framework:

- Fast, isolated, reproducible tests
- Database reset between tests (transactions, `RefreshDatabase`-style helpers, or per-test containers)
- Run via a single command: `{{TEST_CMD}}` (see `project/stack.md`)
- Support for filtering by name, parallel execution, and slow-test profiling

```bash
# [EXAMPLE - Pest/PHP - adapt to your test framework]
{{TEST_CMD}}                          # run all tests
{{TEST_CMD}} tests/Feature/HealthTest.php   # specific file
{{TEST_CMD}} --filter="can create a resource"  # by name
{{TEST_CMD}} --parallel               # parallelized
```

### Test File Organization

Tests live alongside or mirror the source tree. Two typical layouts:

```
tests/
├── Feature/          # or "integration" - tests through HTTP/public API
│   └── Http/Controllers/
│       └── ResourceControllerTest.<ext>
└── Unit/             # isolated unit tests
    └── Services/
        └── ResourceServiceTest.<ext>
```

For modular codebases, each module carries its own `tests/` subtree with the same `Feature` / `Unit` split. See `project/architecture.md` for project-specific layout.

### Authentication in Tests

Generate a real authenticated user and pass credentials the same way production clients do (bearer token, session cookie, API key header). Do not bypass auth via test-only mocks - they hide real auth bugs.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
use App\Models\User;

function authenticatedUser(array $attributes = []): array
{
    $user = User::factory()->create($attributes);
    $token = $user->createToken('test-token')->plainTextToken;

    return [$user, $token];
}

it('requires authentication', function () {
    $this->getJson('/api/v1/resources')->assertUnauthorized();
});

it('returns resources for authenticated user', function () {
    [$user, $token] = authenticatedUser();

    $this->withHeader('Authorization', "Bearer {$token}")
        ->getJson('/api/v1/resources')
        ->assertSuccessful();
});
```

---

## Required Test Cases for Every Endpoint

Every API endpoint **MUST** have tests covering all five categories. No exceptions.

### 1. Authentication (401)

Verify unauthenticated requests are rejected for every method (index, store, show, update, destroy).

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
describe('authentication', function () {
    it('returns 401 on index when not authenticated', function () {
        $this->getJson('/api/v1/resources')->assertUnauthorized();
    });

    it('returns 401 on store when not authenticated', function () {
        $this->postJson('/api/v1/resources', ['name' => 'Test'])->assertUnauthorized();
    });

    it('returns 401 on show when not authenticated', function () {
        $resource = Resource::factory()->create();
        $this->getJson("/api/v1/resources/{$resource->id}")->assertUnauthorized();
    });

    it('returns 401 on update when not authenticated', function () {
        $resource = Resource::factory()->create();
        $this->patchJson("/api/v1/resources/{$resource->id}", ['name' => 'x'])->assertUnauthorized();
    });

    it('returns 401 on destroy when not authenticated', function () {
        $resource = Resource::factory()->create();
        $this->deleteJson("/api/v1/resources/{$resource->id}")->assertUnauthorized();
    });
});
```

### 2. Authorization (403)

Verify users cannot access resources they do not own, and cannot perform actions forbidden by their plan/role.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
describe('authorization', function () {
    it('returns 403 when user tries to view another user resource', function () {
        [$user, $token] = authenticatedUser();
        $other = User::factory()->create();
        $resource = Resource::factory()->for($other)->create();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->getJson("/api/v1/resources/{$resource->id}")
            ->assertForbidden();
    });

    it('returns 403 when free user exceeds quota', function () {
        [$user, $token] = authenticatedUser(['plan' => 'free']);
        Resource::factory()->count(10)->for($user)->create();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->postJson('/api/v1/resources', [...validPayload()])
            ->assertForbidden();
    });
});
```

### 3. Validation (422)

Use parameterized data (datasets / table tests) to cover every validation rule efficiently.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
it('fails validation on store', function (array $payload, array $errors) {
    [$user, $token] = authenticatedUser();

    $this->withHeader('Authorization', "Bearer {$token}")
        ->postJson('/api/v1/resources', $payload)
        ->assertUnprocessable()
        ->assertJsonValidationErrors($errors);
})->with([
    'name is required'     => [['type' => 'foo'], ['name']],
    'name max 255'         => [['name' => str_repeat('a', 256), 'type' => 'foo'], ['name']],
    'type must be valid'   => [['name' => 'Test', 'type' => 'invalid'], ['type']],
]);
```

### 4. Happy Path (200/201)

Verify the endpoint produces the documented response shape, persists the documented side effects, and filters/paginates correctly.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
describe('happy path', function () {
    it('returns paginated list of user resources', function () {
        [$user, $token] = authenticatedUser();
        Resource::factory()->count(3)->for($user)->create();
        Resource::factory()->count(2)->create(); // other users

        $response = $this->withHeader('Authorization', "Bearer {$token}")
            ->getJson('/api/v1/resources')
            ->assertSuccessful();

        $response->assertJsonCount(3, 'data');
        $response->assertJsonStructure([
            'data' => ['*' => ['id', 'name', 'created_at', 'updated_at']],
            'links',
            'meta'  => ['current_page', 'last_page', 'per_page', 'total'],
        ]);
    });

    it('creates a resource and returns resource payload', function () {
        [$user, $token] = authenticatedUser();

        $response = $this->withHeader('Authorization', "Bearer {$token}")
            ->postJson('/api/v1/resources', ['name' => 'New Item'])
            ->assertStatus(Response::HTTP_CREATED);

        $response->assertJsonPath('data.name', 'New Item');
        expect(Resource::query()->where('user_id', $user->id)->count())->toBe(1);
    });

    it('soft deletes a resource', function () {
        [$user, $token] = authenticatedUser();
        $resource = Resource::factory()->for($user)->create();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->deleteJson("/api/v1/resources/{$resource->id}")
            ->assertNoContent();

        expect(Resource::query()->find($resource->id))->toBeNull();
        expect(Resource::withTrashed()->find($resource->id))->not->toBeNull();
    });
});
```

### 5. Not Found (404)

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
describe('not found', function () {
    it('returns 404 for non-existent resource on show', function () {
        [$user, $token] = authenticatedUser();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->getJson('/api/v1/resources/99999')
            ->assertNotFound();
    });

    it('returns 404 for non-existent resource on update', function () {
        [$user, $token] = authenticatedUser();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->patchJson('/api/v1/resources/99999', ['name' => 'x'])
            ->assertNotFound();
    });

    it('returns 404 for non-existent resource on delete', function () {
        [$user, $token] = authenticatedUser();

        $this->withHeader('Authorization', "Bearer {$token}")
            ->deleteJson('/api/v1/resources/99999')
            ->assertNotFound();
    });
});
```

---

## Arrange-Act-Assert (AAA) Pattern

Every test should follow AAA structure clearly:

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
it('calculates completion rate correctly', function () {
    // Arrange
    [$user, $token] = authenticatedUser();
    $resource = Resource::factory()->for($user)->create();
    ResourceEntry::factory()->for($resource)->count(10)->create();
    ResourceEntry::factory()->for($resource)->completed()->count(7)->create();

    // Act
    $response = $this->withHeader('Authorization', "Bearer {$token}")
        ->getJson("/api/v1/resources/{$resource->id}");

    // Assert
    $response->assertSuccessful();
    $response->assertJsonPath('data.completion_rate', 70.0);
});
```

---

## DAMP Over DRY

Prefer **Descriptive And Meaningful Phrases** over DRY in tests. Some duplication is acceptable if it makes each test self-contained and readable without scrolling to a `beforeEach` block.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]

// GOOD: Self-contained, readable
it('rejects unauthenticated access to resources list', function () {
    $this->getJson('/api/v1/resources')->assertUnauthorized();
});

it('rejects unauthenticated access to single resource', function () {
    $resource = Resource::factory()->create();
    $this->getJson("/api/v1/resources/{$resource->id}")->assertUnauthorized();
});

// BAD: Shared mutable state obscures context
// beforeEach(fn () => $this->resource = Resource::factory()->create());
```

---

## Factory States / Test Data Builders

Define named factory states for common test scenarios. Every model (or its equivalent) needs a factory.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
class ResourceFactory extends Factory
{
    public function definition(): array
    {
        return [
            'user_id' => User::factory(),
            'name'    => fake()->words(3, true),
            'type'    => fake()->randomElement(ResourceType::cases()),
            'is_public' => false,
        ];
    }

    public function public(): static   { return $this->state(['is_public' => true]); }
    public function expired(): static  { return $this->state(['ends_at' => now()->subDay()]); }
    public function active(): static   { return $this->state(['ends_at' => now()->addMonth()]); }
}
```

JavaScript/TypeScript equivalents: `fishery`, `factory-bot-ts`.
Python equivalents: `factory-boy`, `pytest` fixtures.
Go equivalents: table-driven tests with builder helpers.

---

## Unit Tests

For isolated service/domain logic, use unit tests against the public API of the class.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]
describe('CompletionCalculator', function () {
    it('returns 0 when there are no entries', function () {
        expect((new CompletionCalculator())->calculate(total: 0, completed: 0))->toBe(0.0);
    });

    it('calculates percentage correctly', function () {
        expect((new CompletionCalculator())->calculate(total: 10, completed: 7))->toBe(70.0);
    });

    it('rounds to one decimal place', function () {
        expect((new CompletionCalculator())->calculate(total: 3, completed: 1))->toBe(33.3);
    });
});
```

---

## Assertions Cheat Sheet

Use specific status methods - never magic numbers. If the framework does not provide a named assertion, use HTTP status constants from the framework/standard library, never numeric literals.

```php
// [EXAMPLE - Pest/PHP - adapt to your test framework]

// GOOD
->assertSuccessful()        // 2xx
->assertOk()                // 200
->assertCreated()           // 201
->assertNoContent()         // 204
->assertUnauthorized()      // 401
->assertForbidden()         // 403
->assertNotFound()          // 404
->assertUnprocessable()     // 422

// For status codes without a dedicated method - use constants
use Symfony\Component\HttpFoundation\Response;
->assertStatus(Response::HTTP_CONFLICT)  // 409

// BAD - magic numbers
->assertStatus(200)
->assertStatus(422)
```

TypeScript/supertest equivalent: `.expect(StatusCodes.CREATED)` (from `http-status-codes` or `node:http.STATUS_CODES`).
Python/httpx equivalent: `assert response.status_code == HTTPStatus.CREATED` (from `http` stdlib).
Go equivalent: `assert.Equal(t, http.StatusCreated, rec.Code)`.

---

## Frontend Testing (MANDATORY)

Frontend tests are **not optional**. Every frontend page, hook, and significant component MUST have tests.

### Framework

Whatever the project's `project/stack.md` specifies. Typical: **Vitest + React Testing Library** for web, **Jest + RNTL** for React Native.

```bash
# Run all frontend tests
{{FRONTEND_TEST_CMD}}

# Run specific file
{{FRONTEND_TEST_CMD}} src/pages/settings/GeneralPage.test.tsx
```

### Rules

- **Tests validate real behavior** - form submission, API calls, navigation, error states
- **No fake tests** - tests that pass just to satisfy a checklist are worse than no tests. Every test must be able to catch a real bug.
- **If a test fails, fix the code** - do not weaken the test to make it pass (unless the test itself is genuinely wrong)
- **Run frontend tests before claiming work is done** - alongside the production build
- **Test what matters**: auth flow, form validation, API error handling, loading/empty states, navigation guards

### What to test per page

| Category | What to test |
|----------|-------------|
| Rendering | Page renders without errors, key elements visible |
| Forms | Validation, submission, success/error feedback |
| API integration | Loading state, data display, error handling |
| Auth | Protected routes redirect, role guards work |
| Navigation | Links work, redirects fire correctly |

### Example

```tsx
// [EXAMPLE - Vitest + React Testing Library]
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ResourceCard } from './ResourceCard';

describe('ResourceCard', () => {
  const mock = { id: '1', name: 'Morning', typeLabel: 'Daily', completionRate: 75 };

  it('renders name and type', () => {
    render(<ResourceCard resource={mock} onSelect={vi.fn()} />);
    expect(screen.getByText('Morning')).toBeInTheDocument();
    expect(screen.getByText('Daily')).toBeInTheDocument();
  });

  it('calls onSelect when clicked', async () => {
    const onSelect = vi.fn();
    render(<ResourceCard resource={mock} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onSelect).toHaveBeenCalledWith('1');
  });

  it('shows selected state', () => {
    render(<ResourceCard resource={mock} onSelect={vi.fn()} isSelected />);
    expect(screen.getByRole('button')).toHaveClass('resource-card--selected');
  });
});
```

---

## End-to-End Testing

E2E tests cover critical user journeys with a real browser (or device simulator for mobile). Typical tooling: **Playwright** or **Cypress** for web, **Detox** or **Maestro** for React Native.

```typescript
// [EXAMPLE - Playwright]
import { test, expect } from '@playwright/test';

test.describe('Resource creation flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('[name="email"]', 'test@example.com');
    await page.fill('[name="password"]', 'password');
    await page.click('button[type="submit"]');
    await page.waitForURL('/dashboard');
  });

  test('user can create a resource', async ({ page }) => {
    await page.click('[data-testid="create-resource"]');
    await page.fill('[name="name"]', 'New Item');
    await page.click('button[type="submit"]');

    await expect(page.locator('[data-testid="resource-card"]')).toContainText('New Item');
  });
});
```

---

## Test Anti-Patterns

- **No logic in tests** - no `filter()`, `map()`, `sum()`, ternaries in assertions. Use explicit expected values.
- **Test state, not interactions** - prefer checking DB state and return values over mock call counts
- **No testing private methods** - test through the public API only
- **No redundant test cases** - same behavior with slightly different data means use datasets / table tests
- **No vague descriptions** - "it works", "handles errors" are unacceptable. Describe behavior and outcome: "it returns 403 when user lacks resource ownership"
- **No shared mutable state** - each test must be independent and repeatable
- **No sleeping in tests** - use proper async/await patterns, polling helpers, or database-state assertions

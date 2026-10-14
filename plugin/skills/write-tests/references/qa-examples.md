# QA Examples: Five Mandatory Categories + E2E

Reference examples for the five mandatory test categories every API endpoint
must cover, plus test organization, assertions, and end-to-end tests. All
snippets are `[EXAMPLE - <framework>]` - the same discipline applies
regardless of stack: Pest, Vitest, Jest, pytest, RSpec, go test, JUnit, etc.

## 1. Authentication (401)

Verify unauthenticated requests are rejected on every protected endpoint.

[EXAMPLE - Pest]

```php
it('returns 401 when not authenticated', function () {
    $this->getJson('/api/v1/examples')->assertUnauthorized();
});
```

## 2. Authorization (403)

Verify users cannot access resources they do not own or lack permission for.

[EXAMPLE - Pest]

```php
it('returns 403 when user lacks permission', function () {
    $user = User::factory()->create();
    $other = Example::factory()->create(); // belongs to someone else

    $this->actingAs($user)
        ->getJson("/api/v1/examples/{$other->id}")
        ->assertForbidden();
});
```

## 3. Validation (422)

Every validator rule must be tested. Use datasets / parameterized tests to
cover all rules efficiently.

[EXAMPLE - Pest with dataset]

```php
it('fails validation on store', function (array $data, array $errors) {
    $user = User::factory()->create();

    $this->actingAs($user)
        ->postJson('/api/v1/examples', $data)
        ->assertJsonValidationErrors($errors);
})->with([
    'name is required' => [['name' => ''], ['name']],
    'name max 255'     => [['name' => str_repeat('a', 256)], ['name']],
    'type is required' => [['name' => 'Test'], ['type']],
    'type must be valid' => [['name' => 'Test', 'type' => 'invalid'], ['type']],
]);
```

## 4. Happy Path (200/201)

Verify the successful case: correct status, correct response shape, correct
persisted state.

[EXAMPLE - Pest]

```php
it('creates an example and returns resource', function () {
    $user = User::factory()->create();
    $data = ['name' => 'Example', 'type' => 'daily'];

    $this->actingAs($user)
        ->postJson('/api/v1/examples', $data)
        ->assertCreated()
        ->assertJsonPath('data.name', 'Example')
        ->assertJsonStructure(['data' => ['id', 'name', 'type', 'created_at']]);

    expect(Example::query()->where('name', 'Example')->exists())->toBeTrue();
});
```

## 5. Not Found (404)

Verify nonexistent resources return 404, not 500.

[EXAMPLE - Pest]

```php
it('returns 404 for non-existent resource', function () {
    $user = User::factory()->create();

    $this->actingAs($user)
        ->getJson('/api/v1/examples/99999')
        ->assertNotFound();
});
```

## Plus: Edge Cases

On top of the five mandatory categories, cover empty states, boundary values,
and concurrency scenarios where relevant.

[EXAMPLE - Pest]

```php
it('returns empty list when user has no items', function () {
    $user = User::factory()->create();

    $this->actingAs($user)
        ->getJson('/api/v1/examples')
        ->assertSuccessful()
        ->assertJsonCount(0, 'data');
});

it('does not return other users items', function () {
    $user = User::factory()->create();
    $otherUser = User::factory()->create();
    Example::factory()->count(3)->for($otherUser)->create();

    $this->actingAs($user)
        ->getJson('/api/v1/examples')
        ->assertJsonCount(0, 'data');
});
```

## Test Organization

Group tests by endpoint / feature using the test framework's grouping
construct (`describe` in Pest/Vitest/Jest, `class` in pytest, `context` in
RSpec, subtests in go test).

[EXAMPLE - Pest]

```php
describe('Example API', function () {
    describe('GET /api/v1/examples', function () {
        it('returns 401 when not authenticated', function () { /* ... */ });
        it('returns paginated list', function () { /* ... */ });
        it('filters by type', function () { /* ... */ });
        it('returns empty list for new user', function () { /* ... */ });
    });

    describe('POST /api/v1/examples', function () {
        it('returns 401 when not authenticated', function () { /* ... */ });
        it('fails validation on store', function () { /* ... */ })->with([/* ... */]);
        it('creates a resource', function () { /* ... */ });
    });
});
```

## Assertions

- Use specific status assertions, never magic numbers
  - `assertUnauthorized()` / `assertForbidden()` / `assertUnprocessable()` / `assertNotFound()` / `assertSuccessful()` / `assertCreated()` in Pest/PHPUnit
  - `expect(response.status_code).toBe(401)` - OK only if no named alias exists in your framework
- Structured JSON assertions: `assertJsonCount()`, `assertJsonPath()`, `assertJsonStructure()`, `assertJsonValidationErrors()` (or the framework equivalents)
- Prefer `expect()`-style / BDD assertions over class-based `$this->assert*()` where the framework supports both

## End-to-End Tests

[EXAMPLE - Playwright]

```typescript
test.describe('Example page', () => {
  test('user can create a new example', async ({ page }) => {
    await page.goto('/login');
    await page.fill('[name="email"]', 'test@example.com');
    await page.fill('[name="password"]', 'password');
    await page.click('button[type="submit"]');

    await page.goto('/examples');
    await page.click('text=New Example');
    await page.fill('[name="name"]', 'Example');
    await page.selectOption('[name="type"]', 'daily');
    await page.click('button[type="submit"]');

    await expect(page.locator('text=Example')).toBeVisible();
  });
});
```

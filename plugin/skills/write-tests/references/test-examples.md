# Test Examples: Skeleton, Framework Snippets, Service Unit Tests

Concrete test code shapes referenced from SKILL.md. Adapt to the project's
actual framework; the discipline (five categories, AAA, DAMP) stays the same
regardless of stack.

## Endpoint Test Skeleton (Pseudocode)

```
describe("Resource API"):

    describe("GET /api/v1/resources"):

        it("returns 401 when not authenticated"):
            getJson("/api/v1/resources")
                .assertUnauthorized()

        it("returns paginated list of owned resources"):
            (user, token) = authenticatedUser()
            factory(Resource).count(3).create(user_id=user.id)
            factory(Resource).count(2).create()  // other users - must NOT appear

            response = getJson("/api/v1/resources", token=token)
                .assertOk()

            response.assertJsonCount(3, "data")
            response.assertJsonStructure(["data", "meta", "links"])

        it("filters by type"):
            (user, token) = authenticatedUser()
            factory(Resource).count(2).create(user_id=user.id, type="daily")
            factory(Resource).create(user_id=user.id, type="weekly")

            getJson("/api/v1/resources?type=daily", token=token)
                .assertOk()
                .assertJsonCount(2, "data")

    describe("POST /api/v1/resources"):

        it("returns 401 when not authenticated"):
            postJson("/api/v1/resources", { name: "Test" })
                .assertUnauthorized()

        it("fails validation with invalid data", datasets=[
            ("name is required",            { type: "daily",  starts_at: future_date },  ["name"]),
            ("name max 255",                { name: "a"*256, type: "daily", starts_at: future_date }, ["name"]),
            ("type must be valid enum",     { name: "Test", type: "hourly", starts_at: future_date }, ["type"]),
            ("starts_at is required",       { name: "Test", type: "daily" }, ["starts_at"]),
        ]):
            (user, token) = authenticatedUser()
            postJson("/api/v1/resources", data, token=token)
                .assertUnprocessable()
                .assertJsonValidationErrors(expected_errors)

        it("creates a resource"):
            (user, token) = authenticatedUser()
            data = { name: "New", type: "daily", starts_at: tomorrow() }

            postJson("/api/v1/resources", data, token=token)
                .assertStatus(CREATED)
                .assertJsonPath("data.name", "New")

            assert Resource.where(name="New", user_id=user.id).exists()

    describe("GET /api/v1/resources/{id}"):

        it("returns 401 when not authenticated")
        it("returns 403 when accessing another user's resource")
        it("returns 404 for non-existent resource")
        it("returns resource with relationships")

    describe("PATCH /api/v1/resources/{id}"):

        it("returns 401 when not authenticated")
        it("returns 403 when updating another user's resource")
        it("updates only the provided fields")

    describe("DELETE /api/v1/resources/{id}"):

        it("returns 401 when not authenticated")
        it("returns 403 when deleting another user's resource")
        it("soft-deletes the resource")
        it("returns 404 when deleting an already-deleted resource")
```

## Concrete Framework Examples

`[EXAMPLE - Laravel + Pest]`

```php
it('returns 401 when not authenticated', function () {
    $this->getJson('/api/v1/resources')->assertUnauthorized();
});

it('returns paginated list of owned resources', function () {
    [$user, $token] = authenticatedUser();
    Resource::factory()->count(3)->create(['user_id' => $user->id]);
    Resource::factory()->count(2)->create();

    $this->withHeader('Authorization', "Bearer {$token}")
        ->getJson('/api/v1/resources')
        ->assertOk()
        ->assertJsonCount(3, 'data')
        ->assertJsonStructure(['data' => ['*' => ['id', 'name', 'type']], 'meta', 'links']);
});

it('fails validation', function (array $data, array $errors) {
    [$user, $token] = authenticatedUser();

    $this->withHeader('Authorization', "Bearer {$token}")
        ->postJson('/api/v1/resources', $data)
        ->assertUnprocessable()
        ->assertJsonValidationErrors($errors);
})->with([
    'name required' => [['type' => 'daily', 'starts_at' => '2099-01-01'], ['name']],
    'type enum'     => [['name' => 'x', 'type' => 'hourly', 'starts_at' => '2099-01-01'], ['type']],
]);
```

`[EXAMPLE - Vitest + supertest]`

```ts
describe('GET /api/v1/resources', () => {
  it('returns 401 when not authenticated', async () => {
    const res = await request(app).get('/api/v1/resources');
    expect(res.status).toBe(StatusCodes.UNAUTHORIZED);
  });

  it('returns paginated list of owned resources', async () => {
    const { user, token } = await authenticatedUser();
    await resourceFactory.createList(3, { userId: user.id });
    await resourceFactory.createList(2);

    const res = await request(app)
      .get('/api/v1/resources')
      .set('Authorization', `Bearer ${token}`);

    expect(res.status).toBe(StatusCodes.OK);
    expect(res.body.data).toHaveLength(3);
  });
});
```

`[EXAMPLE - pytest + httpx]`

```python
def test_returns_401_when_not_authenticated(client):
    response = client.get('/api/v1/resources')
    assert response.status_code == HTTPStatus.UNAUTHORIZED

def test_returns_paginated_list(client, authenticated_user, resource_factory):
    user, token = authenticated_user()
    resource_factory.create_batch(3, user_id=user.id)
    resource_factory.create_batch(2)

    response = client.get('/api/v1/resources', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()['data']) == 3
```

## Service Unit Tests (Pseudocode)

Service unit tests exercise business logic directly, without the HTTP layer.

```
describe("ResourceService"):

    describe("list"):
        it("returns only resources owned by the user"):
            user      = factory(User).create()
            otherUser = factory(User).create()
            factory(Resource).count(3).create(user_id=user.id)
            factory(Resource).count(2).create(user_id=otherUser.id)

            result = service.list(user)

            assert result.total == 3

        it("applies type filter")
        it("applies search filter with prefix match")
        it("paginates with per_page")

    describe("create"):
        it("creates a resource in a transaction")
        it("rolls back when a child write fails")

    describe("delete"):
        it("soft-deletes the resource")
```

## Required Scenarios Reference Table

| Scenario | HTTP | Assertion |
|---|---|---|
| Unauthenticated | 401 | `assertUnauthorized()` |
| Unauthorized (wrong user/role) | 403 | `assertForbidden()` |
| Invalid input - one row per rule | 422 | `assertJsonValidationErrors(['field'])` |
| Happy path | 200 / 201 | Status + JSON structure + DB state |
| Resource not found | 404 | `assertNotFound()` |
| Empty collection | 200 | `assertJsonCount(0, 'data')` |
| Filtering works | 200 | Correct count after filter |
| Pagination works | 200 | Correct `meta.total`, `meta.per_page` |
| Ownership isolation | 200 | Only own resources returned |

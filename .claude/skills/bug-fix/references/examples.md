# Bug Fix: Reference Examples

## Failing test that reproduces the bug (RED phase)

Pseudocode shape:

```
describe("Bug: <short description of correct behavior>"):
    it("<asserts the correct behavior - not the bug number>"):
        // Arrange - set up exactly the conditions from the bug report
        (user, token) = authenticatedUser()
        // Act - make the call that reproduces the bug
        response = makeRequest(token)
        // Assert - what the correct behavior is (fails before the fix)
        assertCorrectBehavior(response)
```

[EXAMPLE - Laravel + Pest]

```php
describe('Bug: entries not filtered by date range', function () {
    it('returns only entries within the specified date range', function () {
        [$user, $token] = authenticatedUser();
        $resource = Resource::factory()->for($user)->create();

        Entry::factory()->for($resource)->create(['date' => '2026-01-15']);
        Entry::factory()->for($resource)->create(['date' => '2026-02-15']);
        Entry::factory()->for($resource)->create(['date' => '2026-03-15']);

        $this->withHeader('Authorization', "Bearer {$token}")
            ->getJson("/api/v1/resources/{$resource->id}/entries?from=2026-01-01&to=2026-01-31")
            ->assertOk()
            ->assertJsonCount(1, 'data');
    });
});
```

[EXAMPLE - Vitest + supertest]

```ts
describe('Bug: entries not filtered by date range', () => {
  it('returns only entries within the specified date range', async () => {
    const { user, token } = await authenticatedUser();
    const resource = await resourceFactory.create({ userId: user.id });

    await entryFactory.create({ resourceId: resource.id, date: '2026-01-15' });
    await entryFactory.create({ resourceId: resource.id, date: '2026-02-15' });
    await entryFactory.create({ resourceId: resource.id, date: '2026-03-15' });

    const res = await request(app)
      .get(`/api/v1/resources/${resource.id}/entries?from=2026-01-01&to=2026-01-31`)
      .set('Authorization', `Bearer ${token}`);

    expect(res.status).toBe(StatusCodes.OK);
    expect(res.body.data).toHaveLength(1);
  });
});
```

## Minimal fix shapes

[EXAMPLE - missing date filter]

```pseudo
// BEFORE (buggy): no date filter
listEntries(resource, filters):
    return resource.entries.with("item").orderBy("date").get()

// AFTER (fixed): date range filter applied
listEntries(resource, filters):
    return resource.entries
        .with("item")
        .when(filters.from, q -> q.where("date", ">=", filters.from))
        .when(filters.to,   q -> q.where("date", "<=", filters.to))
        .orderBy("date")
        .get()
```

[EXAMPLE - wrong denominator]

```pseudo
// BEFORE (buggy): divides by total days in range
totalDays = startDate.diffInDays(endDate) + 1
rate      = completedCount / totalDays

// AFTER (fixed): divides by applicable days only
applicableDays = countApplicableDays(item, startDate, endDate)
rate           = applicableDays > 0 ? completedCount / applicableDays : 0.0
```

[EXAMPLE - missing null check]

```pseudo
// BEFORE (buggy): crashes when template is null
return resource.template.name

// AFTER (fixed): safe null access
return resource.template?.name
```

## Edge case test ideas (after the fix)

```
it("returns empty collection when no entries match the date range")
it("returns all entries when no date filter is provided")
it("handles from without to")          // only lower bound set
it("handles to without from")          // only upper bound set
it("includes entries on exact boundary dates")
```

## Root cause quick map

| Symptom | Likely cause | Where to look |
|---|---|---|
| Wrong data returned | Missing WHERE clause | Service query method |
| Too many results | Missing filter / scope | Controller or service |
| 500 error | Null reference, missing migration | Model, service |
| Wrong calculation | Incorrect formula, off-by-one | Service method |
| Missing data in response | Relationship not loaded | Controller or resource |
| Auth bypass | Missing middleware or authorize() | Route, validator |
| Validation not catching bad input | Wrong rule syntax | Validator |
| Race condition | Missing transaction / lock | Service |

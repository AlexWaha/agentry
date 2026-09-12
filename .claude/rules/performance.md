# Performance

Performance rules for queries, caching, large datasets, queues, and indexing strategy. These apply regardless of stack - framework-specific code blocks are marked as `[EXAMPLE]`.

---

## N+1 Prevention

The single most common performance issue in any ORM-backed application. Always eager load the relationships you will touch.

### Eager Load Up Front

Load everything the response needs in one query tree, not lazily inside a loop.

### `[EXAMPLE - Laravel / Eloquent]`

```php
// GOOD - 3 queries total
Model::query()
    ->with(['items', 'template', 'items.completions'])
    ->paginate();

// BAD - 2N+1 queries
$models = Model::query()->paginate();
foreach ($models as $model) {
    $model->items;     // N+1!
    $model->template;  // N+1!
}
```

### `[EXAMPLE - Prisma]`

```ts
// GOOD - single query with nested includes
await prisma.model.findMany({
  include: { items: { include: { completions: true } }, template: true },
});
```

### `[EXAMPLE - SQLAlchemy]`

```python
# GOOD - joinedload / selectinload
session.query(Model).options(
    selectinload(Model.items).selectinload(Item.completions),
    joinedload(Model.template),
).all()
```

### `[EXAMPLE - Rails ActiveRecord]`

```ruby
Model.includes(items: :completions, template: []).all
```

### Skip-if-loaded vs Force-reload

Most ORMs have two flavors: "load only if missing" and "force reload". Use the former when you're unsure whether a caller already loaded the relation - it costs nothing if it's already there.

### `[EXAMPLE - Laravel]`

```php
$model->loadMissing(['items', 'template']);          // skip if loaded
$model->loadMissing(['items' => fn ($q) => $q->where('scheduled_at', '>=', now())]);
$model->load('items');                                // force reload
```

---

## Query Optimization

### Prefer the ORM / Query Builder

Drop to raw SQL / driver-level calls only when the ORM cannot express the query (complex window functions, CTEs, database-specific features). For standard CRUD, use the ORM - it gives you parameterization, relation hydration, and optimizer hints for free.

### Select Only What You Need

When you don't need the full row, pick only the columns the caller reads. This reduces network transfer, memory, and hydration cost.

```pseudo
// GOOD
Model.select(['id', 'name', 'type', 'starts_at', 'user_id'])
     .for_user(current_user.id)
     .paginate()
```

### Build Queries Declaratively from Optional Filters

Use a declarative conditional helper rather than `if`/`else` branches that assemble different queries. Every filter is a one-liner that adds a constraint only when present.

### `[EXAMPLE - Laravel `when()`]`

```php
Model::query()
    ->with(['items', 'template'])
    ->when($request->input('type'),   fn ($q, $v) => $q->where('type', $v))
    ->when($request->input('search'), fn ($q, $v) => $q->where('name', 'like', "{$v}%"))
    ->when($request->input('is_active'), fn ($q) => $q->active())
    ->paginate($request->input('per_page', 20));
```

### `[EXAMPLE - Prisma dynamic `where`]`

```ts
const where = {
  ...(type && { type }),
  ...(search && { name: { startsWith: search } }),
  ...(isActive && { active: true }),
};
await prisma.model.findMany({ where, take: perPage });
```

### Aggregate Without Loading Rows

When you need a count / sum / average / existence, ask the database - don't hydrate a collection and count in memory.

### `[EXAMPLE - Laravel]`

```php
// GOOD - subquery aggregates
Model::query()
    ->withCount(['items', 'items as completed_count' => fn ($q) => $q->whereNotNull('completed_at')])
    ->withExists('items as has_items')
    ->withAvg('items as avg_completion_time', 'completion_seconds')
    ->paginate();

// BAD - hydrate all items, count in PHP
$models = Model::with('items')->get();
foreach ($models as $m) { $count = $m->items->count(); }
```

### `[EXAMPLE - generic SQL]`

```sql
SELECT m.*,
       (SELECT COUNT(*) FROM items i WHERE i.model_id = m.id) AS items_count,
       (SELECT COUNT(*) FROM items i WHERE i.model_id = m.id AND i.completed_at IS NOT NULL) AS completed_count
FROM models m;
```

### Computed Columns via Correlated Subquery

When a list needs a derived value (last activity timestamp, next scheduled date), add it as a correlated subquery in the `SELECT` list rather than loading a relation just to pick one field.

### `[EXAMPLE - Laravel `addSelect`]`

```php
Model::query()
    ->addSelect([
        'last_completed_at' => Entry::query()
            ->select('completed_at')
            ->whereColumn('entries.model_id', 'models.id')
            ->whereNotNull('completed_at')
            ->latest('completed_at')
            ->limit(1),
    ])
    ->withCasts(['last_completed_at' => 'datetime'])
    ->paginate();
```

---

## Large Dataset Processing

Never load entire tables into memory. Pick the right iteration strategy:

| Strategy | Use Case | Memory |
|----------|----------|--------|
| Chunked batches (fixed N at a time) | Process N records per batch | Low |
| Chunked-by-ID (`WHERE id > ?`) | Same as chunked, but safe during concurrent updates | Low |
| Cursor / streaming iterator | Stream one record at a time via a generator | Minimal |
| Lazy batches | Stream N records at a time via a generator | Low |

**Rule of thumb:** If you're mutating the rows you're iterating, use ID-based chunking so the `OFFSET` drift doesn't skip or double-process rows.

### `[EXAMPLE - Laravel]`

```php
// ID-based chunking for mutations
Entry::query()
    ->where('scheduled_at', '<', now())
    ->whereNull('completed_at')
    ->chunkById(100, function ($entries) {
        foreach ($entries as $entry) {
            SendReminderNotification::dispatch($entry->id);
        }
    });

// Streaming cursor for read-only scans
Model::query()->where('ends_at', '<', now()->subYear())
    ->cursor()
    ->each(fn (Model $m) => ArchiveModel::dispatch($m->id));
```

### `[EXAMPLE - Prisma cursor pagination]`

```ts
let cursor: number | undefined = undefined;
while (true) {
  const batch = await prisma.entry.findMany({
    take: 100, skip: cursor ? 1 : 0,
    ...(cursor && { cursor: { id: cursor } }),
    where: { scheduledAt: { lt: new Date() }, completedAt: null },
    orderBy: { id: 'asc' },
  });
  if (batch.length === 0) break;
  for (const e of batch) await queue.enqueue('reminder', { id: e.id });
  cursor = batch[batch.length - 1].id;
}
```

### `[EXAMPLE - Python yield_per]`

```python
for entry in session.query(Entry).yield_per(100):
    enqueue_reminder(entry.id)
```

---

## Pagination

List endpoints are paginated. Never return unbounded `get()` / `find_all()` on user-facing endpoints.

- Default page size: 20
- Max page size: capped (e.g. 100) - exposed as `per_page` with a hard ceiling
- Small bounded collections (enum values, timezones, currencies) may skip pagination, but document the upper bound

```pseudo
// GOOD
Model.for_user(current_user.id).paginate(per_page: request.per_page ?? 20)

// ACCEPTABLE - small bounded set
ModelType.cases()

// BAD - unbounded
Model.for_user(current_user.id).all()
```

---

## Transactions

Wrap multi-step database operations that must be atomic in a transaction.

### `[EXAMPLE - Laravel]`

```php
return DB::transaction(function () use ($user, $data) {
    $model = Model::create([...$data, 'user_id' => $user->id]);
    foreach ($data['items'] as $item) {
        $model->items()->create($item);
    }
    return $model->load(['items', 'template']);
});
```

### `[EXAMPLE - Prisma]`

```ts
await prisma.$transaction(async (tx) => {
  const model = await tx.model.create({ data: { ...data, userId: user.id } });
  for (const item of data.items) await tx.item.create({ data: { ...item, modelId: model.id } });
  return model;
});
```

**Rules:**
- Keep transactions short - lock contention grows with transaction length
- Never make external API calls, send emails, or publish events inside a transaction - dispatch a job after commit instead
- Use the framework's "after commit" hook (Laravel `afterCommit()`, Rails `after_commit`, Django `transaction.on_commit`) for side effects that depend on the transaction succeeding

---

## Caching

A distributed cache is essential for hot read paths. Common backends: Redis, Memcached, or the cloud equivalent (ElastiCache, Cloud Memorystore).

Cache with an explicit TTL and an explicit invalidation strategy. "Cache without invalidation" is a bug waiting to happen.

### `[EXAMPLE - Laravel]`

```php
// Single-key cache
$stats = Cache::remember("user:{$user->id}:stats", now()->addMinutes(15),
    fn () => $this->calculateStats($user));

// Tagged cache for group invalidation
$templates = Cache::tags(['templates'])->remember(
    'featured-templates', now()->addHours(1),
    fn () => Template::featured()->with('category')->get());

Cache::forget("user:{$user->id}:stats");
Cache::tags(['templates'])->flush();
```

### `[EXAMPLE - generic Redis client]`

```pseudo
key   = "user:{id}:stats"
value = redis.get(key)
if value is None:
    value = computeStats(id)
    redis.setex(key, 15 * 60, serialize(value))
return deserialize(value)
```

### Caching Guidelines

| Data | Typical TTL | Invalidation Trigger |
|------|-------------|---------------------|
| Slow-changing lists (templates, categories) | 1 hour | On create/update/delete |
| Per-user computed stats | 15 minutes | On the event that changes them |
| Reference data (timezones, currencies) | 24 hours | On deployment |
| Featured / editorial content | 1 hour | On admin change |

**Never cache access-control decisions.** Authorization is evaluated at request time, always.

**Don't cache mutable data with a long TTL without a clear invalidation path.** Prefer short TTL + invalidation over long TTL + "it's fine".

---

## Queue Design

Background jobs must follow strict conventions for reliability and cost.

### Universal Job Rules

- **Primitives only in the constructor** - pass IDs, strings, arrays of scalars. Fetch the full entity inside the handler. Reason: serialized full entities cause "stale data" bugs and bloat queue payloads.
- **Explicit timeout** - every job has a max runtime
- **Retries with backoff** - configure attempt count and exponential backoff
- **Retry deadline** - a job that can't succeed after N minutes should stop retrying (dead-letter it)
- **Overlap prevention** - for jobs keyed to a resource (user, order, file), ensure only one runs at a time per key
- **Rate limiting** - jobs that call external APIs must respect the third-party rate limit
- **After-commit dispatch** - if the job depends on a database write, dispatch after the transaction commits, not before

### `[EXAMPLE - Laravel]`

```php
class ProcessModelJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $timeout = 120;
    public int $tries   = 3;
    public int $backoff = 30;

    public function __construct(
        private readonly int $modelId,    // primitive, not full model
        private readonly string $format,
    ) { $this->onQueue('default'); }

    public function retryUntil(): Carbon { return now()->addMinutes(30); }

    public function middleware(): array
    {
        return [
            new WithoutOverlapping($this->modelId)->releaseAfter(60),
            new RateLimited('processing'),
        ];
    }

    public function handle(): void
    {
        $model = Model::with(['items', 'template'])->findOrFail($this->modelId);
        // ...
    }
}
```

### `[EXAMPLE - BullMQ (Node)]`

```ts
await queue.add('process-model', { modelId, format }, {
  attempts: 3,
  backoff: { type: 'exponential', delay: 30_000 },
  timeout: 120_000,
  removeOnComplete: true,
  removeOnFail: { age: 30 * 60 },
});
```

---

## Index Strategy

### Required Indexes

Every column used in `WHERE`, `JOIN`, or `ORDER BY` must have an index. This is a non-negotiable rule - run `EXPLAIN` on every new query and confirm no full table scans.

### `[EXAMPLE - Laravel migration]`

```php
Schema::create('models', function (Blueprint $table) {
    $table->id();
    $table->foreignId('user_id')->constrained()->cascadeOnDelete();
    $table->foreignId('template_id')->nullable()->constrained()->nullOnDelete();
    $table->string('name');
    $table->string('type');
    $table->timestamp('starts_at');
    $table->timestamp('ends_at')->nullable();
    $table->timestamps();
    $table->softDeletes();

    $table->index(['user_id', 'type', 'deleted_at']);
    $table->index(['user_id', 'starts_at']);
});
```

### Engine-level Concerns

- Your engine's default transactional storage (InnoDB on MySQL, standard tables on PostgreSQL) supports row-level locking - use it for high-concurrency writes
- Foreign keys are enforced natively on all modern relational engines - keep them on
- Composite indexes follow the leftmost-prefix rule: `INDEX (a, b, c)` covers queries filtering `a`, `a+b`, `a+b+c`, but NOT `b` alone or `b+c`

### Composite Index Guidelines

- **Equality before range** - put `=` columns first, `<`, `>`, `BETWEEN` columns last
- **Match your hottest query first** - the index serves the query pattern you run thousands of times, not the one-off
- **Audit before adding** - check existing indexes; duplicate indexes waste write cost

---

## What to Avoid

- Raw SQL / driver-level calls where the ORM suffices
- `.get().count()` / `find_all().length` instead of a proper `COUNT(*)` query
- Any query issued inside a loop over parent rows (N+1)
- `SELECT *` when a handful of columns is enough
- Forcing an eager reload when a skip-if-loaded equivalent exists
- Unbounded `get()` / `find_all()` on user-facing list endpoints
- External API calls inside database transactions
- Large file or image processing in a synchronous request - use a job
- Long-TTL caching of mutable data without an invalidation strategy
- Missing indexes on columns used in `WHERE`, `JOIN`, or `ORDER BY`

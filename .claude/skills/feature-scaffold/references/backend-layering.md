# Backend Layering: Stack Examples

Reference examples for the universal endpoint pipeline:
`Request -> Validator (authorization + field rules) -> Service (business logic) -> Resource/DTO (response)`.
All snippets are `[EXAMPLE - <stack>]` - adapt names and idioms to the project's
actual stack per `.claude/project/stack.md` and `.claude/project/architecture.md`.

## Layer responsibilities

- **Thin controller/handler** does three things only: accept the validated
  request, delegate to a service/use case, return a formatted response.
- **Services** own business logic: transactions, side effects, domain rules.
- **Validators** own field-level rules AND authorization (is this caller allowed?).
  Never inline validation inside a controller.
- **Resources/DTOs** own the response shape. Never return raw entities from an API.
- **Models/Entities** are data containers with relationships and scopes. No
  business logic, no service calls, no side effects.

## Migration

Pseudocode shape:

```
create table <resources>:
    primary key id
    foreign key user_id -> users(id) ON DELETE CASCADE
    string name (required, <=255)
    string type (required)
    boolean is_active (default true)
    created_at, updated_at, soft_deleted_at
    index (user_id, is_active)
    index (type)
```

[EXAMPLE - Laravel]

```php
Schema::create('resources', function (Blueprint $table) {
    $table->id();
    $table->foreignId('user_id')->constrained()->cascadeOnDelete();
    $table->string('name', 255);
    $table->string('type', 50);
    $table->date('starts_at');
    $table->boolean('is_active')->default(true);
    $table->timestamps();
    $table->softDeletes();

    $table->index(['user_id', 'is_active']);
    $table->index('type');
});
```

[EXAMPLE - Prisma]

```prisma
model Resource {
  id         Int       @id @default(autoincrement())
  userId     Int
  name       String    @db.VarChar(255)
  type       String    @db.VarChar(50)
  startsAt   DateTime  @db.Date
  isActive   Boolean   @default(true)
  createdAt  DateTime  @default(now())
  updatedAt  DateTime  @updatedAt
  deletedAt  DateTime?

  user User @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@index([userId, isActive])
  @@index([type])
}
```

## Model / Entity

[EXAMPLE - Laravel]

```php
class Resource extends Model
{
    use HasFactory, SoftDeletes;

    protected $fillable = ['user_id', 'name', 'type', 'starts_at', 'is_active'];

    protected function casts(): array
    {
        return ['starts_at' => 'date', 'is_active' => 'boolean'];
    }

    public function user(): BelongsTo { return $this->belongsTo(User::class); }

    public function scopeActive(Builder $q): Builder   { return $q->where('is_active', true); }
    public function scopeOwnedBy(Builder $q, int $uid): Builder { return $q->where('user_id', $uid); }
}
```

[EXAMPLE - TypeScript domain type]

```ts
export interface Resource {
  id: number;
  userId: number;
  name: string;
  type: ResourceType;
  startsAt: string;    // ISO date
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}
```

## Factory / Test Data Builder

[EXAMPLE - Laravel factory]

```php
class ResourceFactory extends Factory
{
    public function definition(): array
    {
        return [
            'user_id'   => User::factory(),
            'name'      => fake()->sentence(3),
            'type'      => fake()->randomElement(['daily', 'weekly', 'monthly']),
            'starts_at' => fake()->dateTimeBetween('now', '+1 month'),
            'is_active' => true,
        ];
    }

    public function inactive(): static { return $this->state(['is_active' => false]); }
}
```

[EXAMPLE - fishery (TypeScript)]

```ts
export const resourceFactory = Factory.define<Resource>(({ sequence }) => ({
  id: sequence,
  userId: 1,
  name: faker.lorem.sentence(3),
  type: 'daily',
  startsAt: faker.date.future().toISOString(),
  isActive: true,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
}));
```

## Store Validator / Request

[EXAMPLE - Laravel FormRequest]

```php
class StoreResourceRequest extends FormRequest
{
    public function authorize(): bool { return $this->user() !== null; }

    public function rules(): array
    {
        return [
            'name'      => ['required', 'string', 'max:255'],
            'type'      => ['required', Rule::in(['daily', 'weekly', 'monthly'])],
            'starts_at' => ['required', 'date', 'after_or_equal:today'],
            'is_active' => ['sometimes', 'boolean'],
        ];
    }
}
```

[EXAMPLE - Zod schema (TypeScript)]

```ts
export const storeResourceSchema = z.object({
  name: z.string().min(1).max(255),
  type: z.enum(['daily', 'weekly', 'monthly']),
  startsAt: z.coerce.date().min(new Date(new Date().setHours(0, 0, 0, 0))),
  isActive: z.boolean().optional(),
});
```

Update validator: same shape, but all fields optional/partial ("sometimes") and
`authorize()` enforces ownership - the current user must own the resource:

```php
public function authorize(): bool
{
    $resource = $this->route('resource');
    return $resource instanceof Resource && $this->user()->id === $resource->user_id;
}
```

## Service / Use Case

Pseudocode (one method per business operation; transactions around multi-step writes):

```
class ResourceService:
    list(user, filters):
        return query
            .owned_by(user.id)
            .filter_when(filters.type,      q -> q.where(type=filters.type))
            .filter_when(filters.search,    q -> q.where(name like filters.search + '%'))
            .filter_when(filters.is_active, q -> q.where(is_active=filters.is_active))
            .order_by(filters.sort_by ?? 'created_at', filters.sort_order ?? 'desc')
            .paginate(filters.per_page ?? 20)

    create(user, data):
        transaction:
            resource = new Resource(data)
            resource.user_id = user.id
            resource.save()
            return resource

    update(resource, data):
        resource.update(data)
        return resource

    delete(resource):
        resource.delete()   // soft delete
```

[EXAMPLE - Laravel service with transaction]

```php
class ExampleService
{
    public function create(User $user, array $data): Example
    {
        return DB::transaction(function () use ($user, $data) {
            $item = Example::query()->create([
                'user_id' => $user->id,
                ...$data,
            ]);

            if (isset($data['children'])) {
                foreach ($data['children'] as $child) {
                    $item->children()->create($child);
                }
            }

            $item->load(['relationA', 'relationB']);

            return $item;
        });
    }
}
```

## Controller / Handler

Forbidden in controllers: inline validation, business logic, direct DB queries
encoding business rules, side effects (emails, external APIs), transforming
entities into response shape.

Pseudocode:

```
class ResourceController:
    constructor(ResourceService service)

    index(request):
        return ResourceResource.collection(service.list(request.user, request.filters))

    store(StoreResourceRequest request):
        return ResourceResource.make(service.create(request.user, request.validated))
            .withStatus(CREATED)

    show(request, resource):
        authorize(request.user owns resource) or abort(FORBIDDEN)
        return ResourceResource.make(resource)

    update(UpdateResourceRequest request, resource):
        return ResourceResource.make(service.update(resource, request.validated))

    destroy(request, resource):
        authorize(request.user owns resource) or abort(FORBIDDEN)
        service.delete(resource)
        return response(NO_CONTENT)
```

[EXAMPLE - Laravel resource controller]

```php
class ExampleController extends Controller
{
    public function index(ExampleIndexRequest $request): AnonymousResourceCollection
    {
        $items = Example::query()
            ->with(['relationA', 'relationB'])
            ->where('user_id', $request->user()->id)
            ->when($request->input('type'), fn ($q, $type) => $q->where('type', $type))
            ->paginate($request->input('per_page', 20));

        return ExampleResource::collection($items);
    }

    public function store(ExampleStoreRequest $request): ExampleResource
    {
        $item = app(ExampleService::class)->create(
            $request->user(),
            $request->validated()
        );

        return new ExampleResource($item);
    }
}
```

Status codes: use framework constants, never numeric literals.

## API Resource / DTO

Rules: only public-contract fields; conditional relationship loading (skip lazy
loads); stable shape per API version; computed fields live here, not on the model.

[EXAMPLE - Laravel Resource]

```php
class ExampleResource extends JsonResource
{
    public function toArray(Request $request): array
    {
        return [
            'id' => $this->id,
            'name' => $this->name,
            'type' => $this->type->value,
            'is_active' => $this->is_active,
            'created_at' => $this->created_at->toIso8601String(),
            'children' => ChildResource::collection($this->whenLoaded('children')),
        ];
    }
}
```

[EXAMPLE - Express serializer]

```ts
export const toResourceDTO = (r: Resource) => ({
  id: r.id,
  name: r.name,
  type: r.type,
  starts_at: r.startsAt.toISOString().slice(0, 10),
  is_active: r.isActive,
  created_at: r.createdAt,
  updated_at: r.updatedAt,
});
```

## Routes

Versioned prefix (`/api/v1/...`), named identifiers, auth middleware per `{{AUTH_METHOD}}`.

[EXAMPLE - Laravel]

```php
Route::middleware('auth:sanctum')->prefix('v1')->group(function () {
    Route::apiResource('resources', ResourceController::class);
});
```

[EXAMPLE - Express]

```ts
const router = Router();
router.use(requireAuth);
router.get('/resources',        list);
router.post('/resources',       validate(storeResourceSchema), store);
router.get('/resources/:id',    show);
router.patch('/resources/:id',  validate(updateResourceSchema), update);
router.delete('/resources/:id', destroy);
app.use('/api/v1', router);
```

## Tests

[EXAMPLE - Pest; same discipline applies to Vitest / Jest / pytest / RSpec / go test]

```php
describe('Example API', function () {
    it('returns 401 when not authenticated', function () {
        $this->getJson('/api/v1/examples')->assertUnauthorized();
    });

    it('returns paginated list for the authenticated user', function () {
        $user = User::factory()->create();
        Example::factory()->count(3)->for($user)->create();

        $this->actingAs($user)
            ->getJson('/api/v1/examples')
            ->assertSuccessful()
            ->assertJsonCount(3, 'data');
    });
});
```

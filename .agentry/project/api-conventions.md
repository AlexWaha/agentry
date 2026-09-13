# API Conventions

> **[PROJECT-SPECIFIC - REPLACE ME]**
> This document describes the current project's API conventions (Laravel 12 + nwidart/laravel-modules). When adapting the template to a new project, replace this entire file with your own framework-specific API conventions. The root `rules/api-conventions.md` references this file.

> **Note:** This project uses `nwidart/laravel-modules` v12. Controller, Request, and Resource classes live under `Modules/{Module}/app/Http/` instead of `app/Http/`. All conventions below apply identically within each module's namespace.

RESTful API design rules for all backend endpoints.

---

## Versioning

All API endpoints are versioned under `/api/v1/`. Future versions use `/api/v2/`, etc. Routes are registered in the appropriate route files per version.

```php
// routes/api.php or routes/v1/api.php
Route::prefix('api/v1')->middleware(['api', 'auth:sanctum'])->group(function () {
    Route::apiResource('calendars', CalendarController::class);
});
```

## Named Routes

All routes must be named with the `api.` prefix for URL generation:

```php
Route::get('/calendars', [CalendarController::class, 'index'])->name('api.calendars.index');
Route::post('/calendars', [CalendarController::class, 'store'])->name('api.calendars.store');
Route::get('/calendars/{calendar}', [CalendarController::class, 'show'])->name('api.calendars.show');
Route::put('/calendars/{calendar}', [CalendarController::class, 'update'])->name('api.calendars.update');
Route::delete('/calendars/{calendar}', [CalendarController::class, 'destroy'])->name('api.calendars.destroy');
```

Use `route('api.calendars.index')` for URL generation. Never hardcode paths.

## Controllers

### Resource Controllers

Use resource controllers for standard CRUD operations. Controllers must be thin - validate via FormRequest, delegate logic to Services, return API Resources.

```php
class CalendarController extends Controller
{
    public function index(CalendarIndexRequest $request): AnonymousResourceCollection
    {
        $calendars = Calendar::query()
            ->with(['items', 'user'])
            ->when($request->input('search'), fn ($query, $search) =>
                $query->where('name', 'like', "{$search}%")
            )
            ->when($request->input('status'), fn ($query, $status) =>
                $query->where('status', $status)
            )
            ->when($request->input('sortBy'), fn ($query) =>
                $query->orderBy(
                    $request->input('sortBy'),
                    $request->input('sortOrder', 'asc')
                )
            )
            ->paginate($request->input('per_page', 30));

        return CalendarResource::collection($calendars);
    }

    public function store(CalendarStoreRequest $request): CalendarResource
    {
        $calendar = Calendar::query()->create($request->validated());
        $calendar->load(['items', 'user']);

        return new CalendarResource($calendar);
    }

    public function show(Calendar $calendar): CalendarResource
    {
        $calendar->load(['items', 'user']);

        return new CalendarResource($calendar);
    }

    public function update(CalendarUpdateRequest $request, Calendar $calendar): CalendarResource
    {
        $calendar->update($request->validated());
        $calendar->load(['items', 'user']);

        return new CalendarResource($calendar);
    }

    public function destroy(Calendar $calendar): JsonResponse
    {
        $calendar->delete();

        return response()->json(null, Response::HTTP_NO_CONTENT);
    }
}
```

### Single-Action Controllers

Use `__invoke()` for non-CRUD operations:

```php
class ArchiveCalendarController extends Controller
{
    public function __construct(
        private readonly CalendarService $calendarService,
    ) {}

    public function __invoke(ArchiveCalendarRequest $request, Calendar $calendar): CalendarResource
    {
        $this->calendarService->archive($calendar);
        $calendar->load(['items']);

        return new CalendarResource($calendar);
    }
}
```

## Responses

**Always use Eloquent API Resources.** Never return raw models from API endpoints.

```php
class CalendarResource extends JsonResource
{
    public function toArray(Request $request): array
    {
        return [
            'id' => $this->id,
            'name' => $this->name,
            'status' => $this->status,
            'created_at' => $this->created_at,
            'items' => CalendarItemResource::collection($this->whenLoaded('items')),
            'user' => UserResource::make($this->whenLoaded('user')),
        ];
    }
}
```

Use `whenLoaded()` for relationships to avoid N+1 queries and keep responses lean.

## Validation

**Always use FormRequest classes.** Never use inline validation (`$request->validate()`) in controllers.

```php
class CalendarStoreRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user()->can('create', Calendar::class);
    }

    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'description' => ['sometimes', 'nullable', 'string'],
            'status' => ['required', 'string', Rule::in(CalendarStatus::cases())],
            'items' => ['sometimes', 'array'],
            'items.*.name' => ['required', 'string', 'max:255'],
            'items.*.type' => ['required', 'string'],
        ];
    }
}
```

## Pagination

Use Laravel's default pagination via `->paginate()` for list endpoints:

```php
$results = Model::query()
    ->with(['relation'])
    ->paginate($request->input('per_page', 30));

return ModelResource::collection($results);
```

For small, bounded collections (e.g., enum lists, categories), `->get()` is acceptable.

## Conditional Query Building

Use `when()` for optional filters - never use `if` branches:

```php
Model::query()
    ->when($request->input('search'), fn ($query, $search) =>
        $query->where('name', 'like', "{$search}%")
    )
    ->when($request->input('status'), fn ($query, $status) =>
        $query->where('status', $status)
    )
    ->when($request->input('date_from'), fn ($query, $date) =>
        $query->where('created_at', '>=', $date)
    )
    ->when($request->input('date_to'), fn ($query, $date) =>
        $query->where('created_at', '<=', $date)
    )
    ->paginate();
```

## Error Responses

Use standard HTTP status codes consistently:

| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST that creates a resource |
| 204 | No Content | Successful DELETE |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Authenticated but lacking permission |
| 404 | Not Found | Resource does not exist |
| 422 | Unprocessable Entity | Validation errors |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Unhandled exception |

## Route Model Binding

Use implicit route model binding. For slug-based lookups, override `resolveRouteBinding()` on the model:

```php
public function resolveRouteBinding($value, $field = null): ?self
{
    return $this->where('slug', $value)->orWhere('id', $value)->firstOrFail();
}
```

## What NOT to Do

- **No raw model returns** - always wrap in API Resources
- **No inline validation** - always use FormRequests
- **No business logic in controllers** - delegate to Services
- **No hardcoded URLs** - use named routes
- **No `DB::` facade** in controllers - use Eloquent via `Model::query()`

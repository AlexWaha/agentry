# Security

Security rules covering authentication, authorization, input validation, data protection, and common vulnerability prevention. These apply regardless of language or framework - stack-specific examples are marked as `[EXAMPLE]`.

---

## Authentication

Use `{{AUTH_METHOD}}` as the project's authentication mechanism. Common options:

| Option | Typical Use Case |
|--------|------------------|
| **Session cookies** | Server-rendered web apps, SPAs on the same origin |
| **API tokens** (e.g. Laravel Sanctum, Rails `has_secure_token`, Django REST token auth) | Mobile apps, first-party SPAs, simple server-to-server |
| **JWT** (signed, short-lived + refresh token) | Distributed services, public APIs, stateless backends |
| **OAuth2 / OIDC** (e.g. Laravel Passport, Auth0, Keycloak, Okta) | Third-party identity providers, SSO, multi-tenant platforms |

Pick ONE primary method per surface (API, web) and document it in `project/stack.md`. Mixing methods inside the same surface creates security gaps.

### Token Management (universal rules)

- Tokens must have an explicit scope / ability set - never issue "god tokens"
- Tokens must have an expiration. Long-lived tokens require a revocation mechanism
- Revoke all active tokens on password change, email change, or explicit logout
- Current session / token must be revocable independently of other sessions

### `[EXAMPLE - Laravel Sanctum]`

```php
// Create scoped token
$token = $user->createToken('mobile-app', ['calendars:read', 'calendars:write']);

// Revoke on logout (current token only)
$request->user()->currentAccessToken()->delete();

// Revoke all (password change)
$user->tokens()->delete();
```

### `[EXAMPLE - JWT in Node/Express]`

```js
const token = jwt.sign({ sub: user.id, scopes: ['read', 'write'] }, SECRET, { expiresIn: '15m' });
// Refresh tokens stored server-side with revocation list
```

## Authorization

Two layers, always both:

1. **Route / middleware layer** - rejects unauthenticated callers before any handler runs
2. **Request / policy layer** - the validator class or policy decides whether THIS authenticated caller may perform THIS action on THIS resource

Single-tenant apps can keep request-level authorization trivial ("authenticated = allowed") but the class must still exist so future multi-tenant changes have a seam. Never put authorization checks directly in controllers.

## Input Validation

**Always validate through a dedicated validator class / request class** (FormRequest, Zod schema, Pydantic model, Joi, DRF serializer, etc.). Never use inline validation inside a controller / handler. Never trust raw input.

```pseudo
// GOOD - validated data only
data = request.validated()
Model.create(data)

// BAD - accepts any input
Model.create(request.all())

// BAD - inline validation in the handler
validated = request.validate({ name: 'required' })
```

Validators own BOTH field-level rules AND authorization. Reject unexpected / unknown fields by default.

## Secrets and Environment Variables

Secrets live in environment variables locally (`.env`) and in a secrets manager in staging / production. They are only accessed through the project's configuration layer - never read directly from the environment at call sites.

```pseudo
// GOOD - via configuration layer
config('services.stripe.secret')
settings.STRIPE_SECRET       // Django-style
process.env.STRIPE_SECRET via a typed config module

// BAD - reading env directly in business code
env('STRIPE_SECRET')         // Laravel
process.env.STRIPE_SECRET    // Node, outside config module
os.environ['STRIPE_SECRET']  // Python, outside config module

// BAD - hardcoded secret
apiKey = 'sk_live_abc123...'
```

**Rules:**
- Never hardcode API keys, tokens, or passwords
- Never log, dump, or return config values that contain secrets
- Don't read env vars outside the config loader - it's the single source of truth
- Every new secret added to code must also appear in `.env.example` with a placeholder value

## PII Protection

Never include Personally Identifiable Information (email, name, phone, address, IP, payment details) in logs or error messages. Log only stable identifiers.

```pseudo
// GOOD
log.info('User subscription activated', { user_id: user.id })

// BAD - leaks PII
log.info('User signed up', { email: user.email, name: user.name })
```

Error responses must not leak stack traces, SQL, or internal class names to API consumers.

## Rate Limiting

Rate limit every authenticated API endpoint. Key by authenticated user ID (falling back to IP for anonymous endpoints).

- Default public limit: ~60 req/min per identity
- Stricter limits on expensive or abusable endpoints (login, password reset, file upload, search)
- Dedicated limits for outbound calls to third-party APIs (per job / per worker)
- Return `429 Too Many Requests` with `Retry-After` header when limit is exceeded

### `[EXAMPLE - Laravel]`

```php
RateLimiter::for('api', fn (Request $request) =>
    Limit::perMinute(60)->by($request->user()?->id ?: $request->ip())
);
```

### `[EXAMPLE - Express middleware]`

```js
import rateLimit from 'express-rate-limit';
app.use('/api/', rateLimit({ windowMs: 60_000, max: 60, keyGenerator: req => req.user?.id ?? req.ip }));
```

## OWASP Top 10 Awareness

| Vulnerability | Prevention |
|---------------|-----------|
| **Injection** | ORM / query builder with parameterized queries; never interpolate user input into raw SQL / shell / LDAP / XPath |
| **Broken Auth** | `{{AUTH_METHOD}}`, bcrypt/argon2 password hashing, proper session invalidation |
| **Sensitive Data Exposure** | HTTPS only, encryption at rest for sensitive columns, no PII in logs, no secrets in responses |
| **XXE** | Disable external entity resolution in the XML parser |
| **Broken Access Control** | Request-level `authorize()`, policies, deny-by-default |
| **Security Misconfiguration** | `.env` not in VCS, debug mode off in production, security headers set, default credentials rotated |
| **XSS** | Wrap API responses in resource / DTO layer (no raw HTML), escape templates, set CSP header |
| **Insecure Deserialization** | Signed cookies / tokens, validated input, no raw deserialization of untrusted bytes (`unserialize`, `pickle.loads`, `yaml.load`) |
| **Known Vulnerabilities** | Latest dependency versions, `composer audit` / `npm audit` / `pip-audit` / `bundle audit` in CI |
| **Insufficient Logging** | Structured logging with context (no PII), audit trail for admin actions, tamper-resistant log storage |

## Database Security

- Always use the ORM / query builder - it parameterizes by default
- When raw SQL is unavoidable, use bound parameters (never string interpolation)
- Input going into `LIKE`, `ORDER BY`, `LIMIT`, or identifiers (table / column names) must be whitelisted or escaped - parameters alone don't protect these positions

```pseudo
// GOOD - parameterized
db.query('SELECT * FROM users WHERE LOWER(name) = ?', [lower(input)])

// GOOD - ORM, safe by default
User.where({ name: input }).first()

// BAD - SQL injection
db.query("SELECT * FROM users WHERE name = '" + input + "'")
```

## Mass Assignment

Always pass only validated data to model / entity constructors. Define an explicit allow-list of writable fields (fillable / permitted / assignable) even when the framework has a global unguard setting - the allow-list doubles as documentation and a second line of defense.

## Soft Deletes

Use soft deletes by default for user-generated content. Never hard-delete user data without a compliance reason (GDPR right-to-be-forgotten, legal hold) and explicit approval.

Policies must return `false` for force-delete / hard-delete by default.

## Encryption at Rest

Encrypt sensitive model attributes (API tokens, OAuth refresh tokens, secret keys, PII fields subject to compliance) using the framework's built-in encrypted cast / field type. Keys live in the configuration layer - rotated and backed up.

```pseudo
// GOOD - framework-level encrypted field
api_token: encrypted_string
secret_key: encrypted_string
```

## Dangerous Functions - NEVER Use

Absolutely forbidden without explicit CEO approval. These are the single biggest source of RCE vulnerabilities.

**PHP:**
```php
exec($command); shell_exec($command); system($command); passthru($command);
eval($code);
```

**JavaScript / TypeScript:**
```js
eval(code);
new Function(code)();
child_process.exec(userInput);        // use execFile with array args instead
```

**Python:**
```python
eval(code); exec(code);
os.system(cmd);
subprocess.run(cmd, shell=True)       # use shell=False with arg list
pickle.loads(untrusted_bytes); yaml.load(untrusted)  # use yaml.safe_load
```

**Ruby:**
```ruby
eval(code); instance_eval(code);
system("cmd #{user_input}"); `cmd #{user_input}`
Marshal.load(untrusted_bytes)
```

**Shell:**
```bash
# FORBIDDEN - command injection
bash -c "something $USER_INPUT"
eval "$USER_INPUT"
```

## Debug Functions - NEVER Commit

These must never appear in committed code.

**PHP:** `dd()`, `dump()`, `ray()`, `var_dump()`, `print_r()`, `error_log($x)` for debugging
**JavaScript / TypeScript:** `console.log()`, `console.dir()`, `console.trace()`, `debugger;`, `alert()`
**Python:** `print()` in library code, `breakpoint()`, `pdb.set_trace()`, `ipdb.set_trace()`
**Go:** `fmt.Println()`, `fmt.Printf()` in production code paths (use `log` / `slog`)
**Ruby:** `puts`, `p`, `pp`, `binding.pry`, `binding.irb`, `byebug`

Add a pre-commit / CI check that fails the build if any of these appear in a diff.

## File Uploads

Always validate file type (by content, not by extension), size, and MIME type in the validator class:

```pseudo
// pseudocode rule set
avatar:   required, mime in [jpeg, png], max 3 MB
document: required, mime in [pdf, csv], max 10 MB
```

- Verify MIME via magic bytes, not the extension or `Content-Type` header
- Store uploads outside the web root, or in object storage with non-guessable names
- Scan user uploads for malware when the threat model requires it
- Never execute uploaded files, never serve them from a path that allows script execution

## Webhook Security

All incoming webhooks must verify sender identity before processing. The standard pattern is HMAC signature verification with constant-time comparison.

```pseudo
signature = request.header('X-Webhook-Signature')
expected  = hmac_sha256(request.raw_body, config('services.webhook.secret'))

if !constant_time_equals(expected, signature):
    return 401 Unauthorized
```

Rules:
- Use constant-time comparison (`hash_equals`, `crypto.timingSafeEqual`, `hmac.compare_digest`) - never `==`
- Verify the signature over the **raw body**, before any parsing / mutation
- Reject requests older than a replay window (check a timestamp header)
- Rotate webhook secrets on any suspected compromise

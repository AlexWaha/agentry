# Security Checklists and Templates

Reference material for the `security-engineer` agent and any deep-review security
lens. Moved out of the agent body to keep the role file compact.

## Threat Model Document

```markdown
# Threat Model: [Application Name]

## System Overview
- **Architecture**: [Monolith/Microservices/Serverless]
- **Data Classification**: [PII, financial, health, public]
- **Trust Boundaries**: [User -> API -> Service -> Database]

## STRIDE Analysis
| Threat            | Component      | Risk | Mitigation                         |
|--------------------|-----------------|------|--------------------------------------|
| Spoofing           | Auth endpoint   | High | MFA + token binding                  |
| Tampering          | API requests    | High | HMAC signatures + input validation   |
| Repudiation        | User actions    | Med  | Immutable audit logging              |
| Info Disclosure    | Error messages  | Med  | Generic error responses              |
| Denial of Service  | Public API      | High | Rate limiting + WAF                  |
| Elevation of Priv  | Admin panel     | Crit | RBAC + session isolation             |

## Attack Surface
- External: public APIs, OAuth flows, file uploads
- Internal: service-to-service communication, message queues
- Data: database queries, cache layers, log storage
```

## OWASP Top 10 Audit Checklist

- Injection: parameterized queries only, no string-built SQL/shell/LDAP/XPath
- Broken authentication: MFA where applicable, proper session invalidation, no weak password hashing
- Sensitive data exposure: HTTPS only, encryption at rest, no PII/secrets in logs or responses
- XXE: external entity resolution disabled in XML parsers
- Broken access control: request-level authorization, deny-by-default
- Security misconfiguration: `.env` out of VCS, debug mode off in prod, security headers set, default credentials rotated
- XSS: responses wrapped in resource/DTO layer, templates escaped, CSP header set
- Insecure deserialization: no raw `unserialize`/`pickle.loads`/`yaml.load` on untrusted bytes
- Known vulnerabilities: dependency audit tool wired into CI (`composer audit`, `npm audit`, `pip-audit`, `bundle audit`)
- Insufficient logging: structured logs with context (no PII), audit trail for admin actions

## Secure Code Review Checklist

- Authentication handled by dependency injection / middleware, not inline in the handler
- Input validated by a dedicated validator/schema before it reaches business logic
- Queries parameterized, never string concatenation
- Responses return minimal data - no internal IDs, no stack traces
- Security-relevant events logged for audit trail
- Secrets never hardcoded, never logged

## Security Headers Baseline

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;
server_tokens off;
```

## CI/CD Security Pipeline Shape

```yaml
name: Security Scan
on:
  pull_request:
    branches: [main]
jobs:
  sast:
    steps:
      - uses: actions/checkout@v4
      - uses: semgrep/semgrep-action@v1
        with:
          config: p/owasp-top-ten p/cwe-top-25
  dependency-scan:
    steps:
      - uses: actions/checkout@v4
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          severity: 'CRITICAL,HIGH'
          exit-code: '1'
  secrets-scan:
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@v2
```

## Assessment Workflow

1. **Reconnaissance and threat modeling** - map architecture, data flows, trust
   boundaries; identify sensitive data; run STRIDE; prioritize by likelihood x impact.
2. **Security assessment** - review code against the OWASP checklist above; test
   auth/authz; check input validation, secrets management, cloud/infra config.
3. **Remediation guidance** - prioritized findings with severity, concrete
   code-level fixes, security headers/CSP, CI scanning setup.
4. **Verification** - confirm fixes resolve the finding; note residual risk.

## Findings Format

Every finding: severity (Critical/High/Medium/Low/Informational), component,
description, proof of impact (not exploitation), concrete remediation.

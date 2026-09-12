---
name: frontend-standards
description: Engineering standard for React + Tailwind SPAs - layered design tokens (no hardcoding / magic numbers), feature-based architecture with unidirectional imports, a reusable component system (shadcn + cva + cn), type-safe i18n from day one, and config extraction. Use when scaffolding a frontend, building or reviewing React/Tailwind UI, setting up a design system or design tokens, wiring i18n, or enforcing frontend code quality so the result is consistent, maintainable, and human-readable.
allowed-tools: Read, Grep, Glob, Write, Edit
---

# Frontend Standards - Maintainable React + Tailwind

Apply this when building or reviewing a React SPA so the code is structured, consistent, and
free of hardcoding. Research-backed (Bulletproof React / Feature-Sliced Design; Tailwind v4
layered tokens; shadcn/cva; type-safe i18n) - sources in
[references/research-sources.md](references/research-sources.md).

Stack-neutral within the React family (React 18/19 + Vite/Next + Tailwind v3/v4 + shadcn/ui or
any headless kit). Adapt paths to the project; the seven principles are non-negotiable.

## The seven principles

1. **No hardcoding / no magic numbers.** Every recurring meaningful value (color, spacing,
   radius, duration, size, z-index, breakpoint, limit, storage key, route, query key) resolves
   to a design token, a named `config/` constant, a route builder, a type/enum, or an i18n key -
   never an inline literal. Test: "if this changed, how many files would I edit?" >1 => extract.
2. **Layered design tokens.** Three layers, each value defined once: raw primitives (hex/px) in
   one tokens file; semantic mapping (`--primary`, `bg-panel`) in a theme file; component
   variants (cva) referencing semantic tokens only. Components use semantic utilities, never raw
   hex or arbitrary `[13px]` where a token exists. See
   [references/design-tokens.md](references/design-tokens.md).
3. **Config extraction + splitting.** Non-token constants live in `src/config/`, one file per
   concern (`nav.ts`, `providers.ts`, `animation.ts`, `layout.ts`...). Split by concern when a
   file grows; config holds data, not behavior.
4. **Reusable-first.** A pattern that appears twice is extracted before the second copy ships -
   a shadcn primitive in `components/ui/` or a composed widget in `components/shared/`. Prefer
   composition into semantic, domain-named components over piling props/variants on one
   component. Use cva for genuine variants (sparingly) and `cn()` to merge classes.
5. **i18n from day one.** Zero hardcoded user-facing strings, even for a single language. All
   copy goes through a centralized, type-safe catalog with `t(key, params?)`; `DEFAULT_LOCALE`
   is a constant, never inlined; keys are namespaced (dot-notation, first segment = namespace).
   Prefer a lightweight homemade module over a heavy i18n library unless the framework needs one.
   See [references/i18n-module.md](references/i18n-module.md).
6. **Feature-based architecture + unidirectional imports.** Layers and allowed direction:
   `shared` (components/ui, components/shared, lib, config, types) -> `features/<x>` ->
   `pages`/`app`. Shared imports nothing feature-specific; a feature imports only from shared and
   itself; pages compose features. Enforce with ESLint `import/no-restricted-paths`; colocate
   files that change together. See [references/architecture-boundaries.md](references/architecture-boundaries.md).
7. **Readability.** Strict TS (no `any`, explicit prop interfaces, explicit return types on
   exports), functional components + hooks only, small components (split at ~150 lines or two
   jobs), descriptive names, no debug artifacts.

## Steps

1. **Detect mode.** Building (scaffold/screen) or reviewing (audit a diff). Read the project's
   frontend rule/overlay first if one exists; this skill is the fallback standard.
2. **Establish the token + theme layer** (build) or confirm it exists (review): raw tokens file,
   semantic theme mapping, keyframes, fonts. No component may introduce a raw hex.
3. **Establish the folder layers**: `components/ui`, `components/shared`, `features/`, `pages/`,
   `app/`, `lib/`, `config/`, `types/`, `services/`, `stores/`, `styles/`. Wire the ESLint
   import-boundary rule.
4. **Wire i18n** before writing any copy: the `t()` module + `DEFAULT_LOCALE` + an English
   catalog split by namespace.
5. **Build/review each unit** against the seven principles and the pre-commit checklist. Reuse
   the design system; extract on the second copy; keep data behind a service + query layer and
   UI state in a store.
6. **Gate.** Real output this session: typecheck + lint (incl. import boundaries) + tests +
   build all green. A passing subset is not a pass.

## Rules

- Never introduce a raw hex or arbitrary `[..]` utility where a token exists - add a token first.
- Never copy-paste a widget or re-hardcode what a shared component already provides.
- Never ship a hardcoded user-facing string - it goes in the i18n catalog.
- Never let a feature import another feature, or shared import a feature - boundaries are one-way.
- Never inline a locale fallback string - use the `DEFAULT_LOCALE` constant.
- Config files hold data only; no component/store imports inside `config/`.

## Quality checklist (pre-commit / review)

- [ ] No inline colors/sizes/durations/z-index/strings - all tokens/config/i18n.
- [ ] No raw hex or arbitrary `[..]` where a token exists.
- [ ] No duplicated widget/logic - reused or extracted.
- [ ] No hardcoded user-facing strings - all via `t()`; keys exist in the catalog.
- [ ] Import boundaries respected (shared !-> features !-> app).
- [ ] Data via services + query layer; UI state in a store; no data-source imports in components.
- [ ] Strict types, explicit prop interfaces, no `any`, no debug artifacts.
- [ ] typecheck + lint + tests + build green from real output.

## References (read only when needed)

- [references/design-tokens.md](references/design-tokens.md) - three-layer token model, Tailwind v4 `@theme` wiring, dark-mode via data-attribute/class.
- [references/i18n-module.md](references/i18n-module.md) - lightweight type-safe `t()` module + catalog layout.
- [references/architecture-boundaries.md](references/architecture-boundaries.md) - feature-based folder layout + ESLint `import/no-restricted-paths` config.
- [references/research-sources.md](references/research-sources.md) - the sourced best-practice references behind this standard.

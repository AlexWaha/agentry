# Research sources

The standard in this skill is distilled from current (2025-2026) best-practice references.

## Architecture (feature-based / unidirectional)

- Bulletproof React - project structure & unidirectional architecture (shared -> features ->
  app), enforced via ESLint import boundaries:
  https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md
- Feature-Sliced Design - layer/slice/segment methodology for scalable frontends:
  https://feature-sliced.design/blog/frontend-folder-structure

Key takeaways: organize by feature not technical type; colocation; strict one-way import
boundaries (shared never imports features); keep UI dumb and data layers smart; a hybrid
feature+shared layout scales best for mid-size apps.

## Design tokens (Tailwind v4)

- Layered tokens with the `@theme` directive:
  https://www.matchkit.io/blog/design-tokens-tailwind-v4

Key takeaways: three layers (raw primitives -> semantic -> component); each raw value defined
once; name tokens by purpose (`--color-primary`), never by literal (`--color-blue-500`); keep
dynamic per-theme values in `:root`/`.dark`/`[data-theme]` and static registration in `@theme`,
eliminating `dark:` modifiers in markup; CSS-first tokens are also more reliable for AI tooling.

## Component system (shadcn / cva)

- shadcn/ui best practices - composition over variants, cva sparingly, cn() for class merging:
  https://rupeshpoudel.com.np/blog/shadcn-best-practices

Key takeaways: you own the code; compose base primitives into semantic domain components rather
than piling on variants; use cva for genuine structured variants only; keep components small;
establish patterns early and stay consistent.

## i18n (type-safe, no hardcoded strings)

- Type-safe internationalization in modern frontends:
  https://leapcell.io/blog/building-type-safe-internationalization-in-modern-frontend-frameworks

Key takeaways: never embed strings in components - even for a single language, establishing the
pattern day one prevents a painful later refactor; centralize the catalog; enforce key/param
contracts with the type system for autocomplete + no missing-key runtime errors; automated
linting can block new hardcoded strings in CI.

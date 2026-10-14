# i18n - lightweight, type-safe, day one

No hardcoded user-facing strings, even for a single language. Establishing the pattern from day
one avoids a painful refactor when a second locale is needed, and centralizes every text edit.

Prefer a ~30-line homemade module over a heavy library (i18next/FormatJS/lingui) unless the
framework genuinely needs one. Single source of truth, full control over the fallback chain,
zero runtime dependency, and it reads cleanly for AI tooling.

## Catalog layout

Split the English base by namespace; first key segment = namespace:

```
src/lib/i18n/
  index.ts            # t(), useTranslation(), DEFAULT_LOCALE, SupportedLocale
  en/
    common.ts         # { actions: { save: "Save", cancel: "Cancel" } }
    nav.ts
    overview.ts
    settings.ts
```

## Public surface

```ts
export const DEFAULT_LOCALE = "en" as const;      // NEVER inline a locale fallback anywhere
export type SupportedLocale = "en";               // add "de" | "fr" ... later

export function t(key: MessageKey, params?: Record<string, string | number>): string;
export function useTranslation(): { t: typeof t; locale: SupportedLocale; setLocale(l: SupportedLocale): void };
```

- `MessageKey` is a union derived from the catalog (`keyof` over the flattened `en` tree), so an
  unknown key is a compile error - autocomplete + no missing-key runtime bugs.
- A missing key returns the key verbatim so regressions are visible in the UI and in review.
- Placeholders use a single convention (`{name}`); resolve params in `t()`.

## Usage

```tsx
const { t } = useTranslation();
return <button>{t("common.actions.save")}</button>;
```

## Rules

- Every user-facing string goes through `t()`. Non-UI strings (enum values, ids, log text, test
  names) are NOT localized.
- `DEFAULT_LOCALE` is the only place a locale literal appears (plus the catalog folder names).
- Adding a language = add a `xx/` catalog with the same keys + extend `SupportedLocale`; no
  component changes. Keep key parity across locales (enforced in review).
- Do not build keys from untrusted/dynamic input unless the segment is a whitelisted enum.
- Do not cache the resolved locale in localStorage/cookies if an authenticated user's preference
  is the source of truth; read it from the user record.

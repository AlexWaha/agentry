# Design tokens - three-layer model

Never let a raw color/size literal live in a component. Three layers, each value defined once.

## Layer 1 - raw primitives (defined once)

Raw hex/px, keyed per theme. In Tailwind v4 put them in a `tokens.css`, scoped to the theme
selector (a `data-theme` attribute or the `.dark` class):

```css
[data-theme="light"] {
  --app-bg:#eeebe3; --panel:#ffffff; --border:#e3ded2;
  --text:#1c2126; --text-2:#5b6470; --accent:#0f7a66; /* ... */
}
[data-theme="dark"] {
  --app-bg:#101418; --panel:#181d22; --border:#2b333b;
  --text:#e9edf0; --text-2:#9aa5b1; --accent:#2ba88b; /* ... */
}
```

A hex value appears in exactly one place. Changing the brand color = editing one line.

## Layer 2 - semantic mapping (purpose-named)

Map raw primitives to purpose names and expose them as utilities. In Tailwind v4 use `@theme
inline` so `bg-panel`, `text-text-2`, `border-border`, `rounded-md` become real classes, and
alias your headless kit's semantic vars (shadcn `--primary`, `--muted`, `--destructive`, ...):

```css
[data-theme="light"], [data-theme="dark"] {
  --background: var(--app-bg); --foreground: var(--text);
  --primary: var(--accent); --primary-foreground: #ffffff;
  --muted: var(--panel-2); --muted-foreground: var(--text-3);
}
@theme inline {
  --color-background: var(--background);
  --color-panel: var(--panel);
  --color-text-2: var(--text-2);
  --color-primary: var(--primary);
  --radius-sm: 7px; --radius-md: 9px; --radius-lg: 12px; --radius-pill: 20px;
  --font-sans: "Geist Variable", system-ui, sans-serif;
}
```

Semantic naming is what makes refactors safe: change one variable, not a codebase-wide find of
`--color-blue-500`. Keep dynamic (per-theme) values in the theme selectors and the static
registration in `@theme`, so `bg-background` resolves per theme with zero `dark:` modifiers in
markup.

## Layer 3 - component variants

Variant maps (cva) reference only semantic utilities:

```ts
const button = cva("inline-flex items-center rounded-md font-[600]", {
  variants: { variant: {
    primary: "bg-primary text-primary-foreground",
    ghost:   "bg-panel border border-border text-text-2",
  } },
});
```

## Rules

- No raw hex or arbitrary `[13px]` in a component when a token exists; if a value has no token,
  ADD a token (layers 1+2) then use it.
- Dark mode via a single attribute/class on the root; do not scatter `dark:` color overrides -
  the token variables already switch.
- Tailwind v4: rebind the `dark` variant to your attribute if you use `data-theme`:
  `@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));`
- Watch headless-kit name collisions (e.g. shadcn `--accent` = hover surface, not brand). Map
  brand to `--primary` and keep the kit's semantic names intact.
- Keyframes/animations belong in one CSS file exposed as `animate-*` utilities - never inline
  `@keyframes` or `style={{animation}}`.

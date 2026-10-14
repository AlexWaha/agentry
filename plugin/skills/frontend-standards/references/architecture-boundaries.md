# Feature-based architecture + import boundaries

Organize by feature/domain, not by technical type. A feature is a self-contained module: if you
delete it, the rest of the app still builds. Colocate files that change together.

## Folder layout

```
src/
  app/               # entry, providers, router  (composes features + shared)
  pages/             # route-level screens        (compose features + shared)
  features/
    <feature>/       # e.g. builder/, tracker/, agents/
      components/    # feature-only UI
      hooks/         # feature-only hooks
      <feature>.store.ts
      types.ts
  components/
    ui/              # design-system primitives (shadcn), SHARED
    shared/          # composed cross-feature widgets, SHARED
  lib/               # utils (cn), query-keys, formatters   SHARED
  config/            # non-token constants, split by concern SHARED
  types/             # domain types                          SHARED
  services/          # data access (mock/real), one seam     SHARED
  stores/            # global UI state                       SHARED
  styles/            # tokens.css, theme.css, keyframes.css
```

## Unidirectional imports

Allowed direction only: `shared -> features -> pages/app`.
- `shared` (components/ui, components/shared, lib, config, types, services, stores) imports
  nothing from `features`, `pages`, or `app`.
- `features/<x>` imports from `shared` and its OWN folder - never from another feature.
- `pages`/`app` compose features + shared.

Keep pages thin: compose + wire data; business/interaction logic lives in feature modules/hooks.

## Enforce with ESLint (`import/no-restricted-paths`)

```js
// eslint.config.js (flat)
import boundaries from "eslint-plugin-import";
export default [{
  plugins: { import: boundaries },
  rules: {
    "import/no-restricted-paths": ["error", { zones: [
      { target: "./src/components", from: "./src/features", message: "shared must not import features" },
      { target: "./src/components", from: "./src/pages" },
      { target: "./src/features",   from: "./src/pages",    message: "features must not import pages" },
      // each feature isolated from siblings:
      { target: "./src/features/builder", from: "./src/features/tracker" },
    ] }],
  },
}];
```

Add one cross-feature zone per sibling pair you want isolated, or generate them. The rule makes
boundary violations a build failure, not a review nicety.

## Data / state discipline

- Server-shaped data flows only through `services/` wrapped in query hooks; components never
  import fixtures or call services directly.
- The mock/real swap point is a single `services/transport.ts`; service signatures mirror the
  real backend/binding so swapping is a body change, not a call-site change.
- UI-only state (theme, nav, dirty, open overlays) lives in stores; do not mix server state into
  the UI store or UI state into the query cache.

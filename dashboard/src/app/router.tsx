import {
  createBrowserHistory,
  createRootRoute,
  createRouter,
  type AnyRoute,
  type RouterHistory,
} from "@tanstack/react-router";

import { NotFoundPage } from "@/app/NotFoundPage";
import { Shell } from "@/app/Shell";
import { ErrorState } from "@/components/shared/ErrorState";
import { parseSearch, stringifySearch } from "@/lib/search-params";

/**
 * What a feature folder provides: `features/<dir>/routes.tsx` exports `createRoutes(parent)`, which returns the
 * code-based routes of that feature (page components loaded with lazy()). The shell never names a feature; it
 * collects them with the glob below (FR-78).
 */
export interface FeatureRoutes {
  createRoutes: (parent: AnyRoute) => AnyRoute[];
}

export const featureModules: Record<string, FeatureRoutes> = import.meta.glob<FeatureRoutes>("../features/*/routes.tsx", {
  eager: true,
});

/** Builds the route tree from feature modules; tests pass stubs instead of the glob result. */
export function buildRouteTree(modules: Record<string, FeatureRoutes>) {
  const rootRoute = createRootRoute({ component: Shell, notFoundComponent: NotFoundPage });
  const children = Object.keys(modules)
    .sort()
    .flatMap((file) => modules[file].createRoutes(rootRoute));
  return rootRoute.addChildren(children);
}

/** The app router. Browser history, scroll restoration on, type not registered (paths come from config/routes). */
export function createAppRouter(modules: Record<string, FeatureRoutes> = featureModules, history?: RouterHistory) {
  return createRouter({
    routeTree: buildRouteTree(modules),
    history: history ?? createBrowserHistory(),
    scrollRestoration: true,
    parseSearch,
    stringifySearch,
    defaultErrorComponent: ({ error, reset }) => <ErrorState error={error} onRetry={reset} className="max-w-xl" />,
  });
}

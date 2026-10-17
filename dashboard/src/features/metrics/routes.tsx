import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateMetricsSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.metrics,
      component: lazyRouteComponent(() => import("./MetricsPage"), "MetricsPage"),
      validateSearch: validateMetricsSearch,
    }),
  ];
}

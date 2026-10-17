import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateActivitySearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.activity,
      component: lazyRouteComponent(() => import("./ActivityPage"), "ActivityPage"),
      validateSearch: validateActivitySearch,
    }),
  ];
}

import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateGraphsSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.graphs,
      component: lazyRouteComponent(() => import("./GraphsPage"), "GraphsPage"),
      validateSearch: validateGraphsSearch,
    }),
  ];
}

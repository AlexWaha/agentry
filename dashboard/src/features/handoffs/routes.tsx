import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateHandoffsSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.handoffs,
      component: lazyRouteComponent(() => import("./HandoffsPage"), "HandoffsPage"),
      validateSearch: validateHandoffsSearch,
    }),
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.handoff,
      component: lazyRouteComponent(() => import("./HandoffPage"), "HandoffPage"),
    }),
  ];
}

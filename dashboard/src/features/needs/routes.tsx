import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.needs,
      component: lazyRouteComponent(() => import("./NeedsPage"), "NeedsPage"),
    }),
  ];
}

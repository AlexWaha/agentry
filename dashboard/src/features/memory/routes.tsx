import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateMemorySearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.memory,
      component: lazyRouteComponent(() => import("./MemoryPage"), "MemoryPage"),
      validateSearch: validateMemorySearch,
    }),
  ];
}

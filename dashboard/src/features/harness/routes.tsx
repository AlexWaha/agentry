import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateHarnessSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.harness,
      component: lazyRouteComponent(() => import("./HarnessPage"), "HarnessPage"),
      validateSearch: validateHarnessSearch,
    }),
  ];
}

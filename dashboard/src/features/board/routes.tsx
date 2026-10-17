import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateBoardSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.board,
      validateSearch: validateBoardSearch,
      component: lazyRouteComponent(() => import("./BoardPage"), "BoardPage"),
    }),
  ];
}

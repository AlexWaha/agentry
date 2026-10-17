import { createRoute, lazyRouteComponent, type AnyRoute } from "@tanstack/react-router";

import { ROUTES } from "@/config/routes";

import { validateTasksSearch } from "./search";

export function createRoutes(parent: AnyRoute): AnyRoute[] {
  return [
    createRoute({
      getParentRoute: () => parent,
      path: ROUTES.tasks,
      validateSearch: validateTasksSearch,
      component: lazyRouteComponent(() => import("./TasksPage"), "TasksPage"),
    }),
  ];
}

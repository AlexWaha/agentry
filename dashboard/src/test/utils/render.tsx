import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";

import { Shell } from "@/app/Shell";
import { createQueryClient } from "@/lib/query-client";

export interface RenderWithAppOptions {
  /** Location to load, path plus search: "/tasks?folder=done&page=2". Default "/". */
  url?: string;
  /** Route pattern the UI is mounted on, for example "/tasks/$taskId". Default: the pathname of `url`. */
  path?: string;
  /** The route's search validation, as a page route would declare it. Default: params pass through as parsed. */
  validateSearch?: (raw: Record<string, unknown>) => object;
  /** Wrap the UI in the real shell (sidebar, top bar, expired-link switch). Default false. */
  shell?: boolean;
  queryClient?: QueryClient;
}

export type AppRouter = ReturnType<typeof buildRouter>;

function buildRouter(ui: ReactNode, options: RenderWithAppOptions) {
  const url = options.url ?? "/";
  const path = options.path ?? new URL(url, "http://localhost").pathname;
  const rootRoute = createRootRoute({ component: options.shell ? Shell : Outlet });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path,
    component: () => <>{ui}</>,
    validateSearch: options.validateSearch ?? ((raw: Record<string, unknown>) => raw),
  });
  return createRouter({
    routeTree: rootRoute.addChildren([pageRoute]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
}

export interface RenderedApp extends RenderResult {
  router: AppRouter;
  queryClient: QueryClient;
}

/**
 * Renders a UI inside the providers the app has: a QueryClient with the production defaults and a router on
 * a memory history. Async because the router loads its matches first; `await` it, then query the screen.
 */
export async function renderWithApp(ui: ReactNode, options: RenderWithAppOptions = {}): Promise<RenderedApp> {
  const queryClient = options.queryClient ?? createQueryClient();
  const router = buildRouter(ui, options);
  await router.load();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient };
}

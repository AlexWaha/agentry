import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRoute, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildRouteTree, createAppRouter, featureModules, type FeatureRoutes } from "@/app/router";
import { PAGE_IDS, ROUTES, type PageId } from "@/config/routes";
import { createQueryClient } from "@/lib/query-client";
import { mockApi, ok } from "@/test/utils/api-mock";
import statusFixture from "@/test/fixtures/shell/status.json";

/** One stub feature that registers a labelled page for every route, as the real features do between them. */
const stubFeature: FeatureRoutes = {
  createRoutes: (parent) =>
    PAGE_IDS.map((id) =>
      createRoute({ getParentRoute: () => parent, path: ROUTES[id], component: () => <h1>stub page {id}</h1> }),
    ),
};

async function renderAt(path: string) {
  mockApi({ "/api/v1/status": ok(statusFixture.body.data), "/api/v1/runs": ok([]) });
  const router = createAppRouter({ "../features/stub/routes.tsx": stubFeature }, createMemoryHistory({ initialEntries: [path] }));
  await router.load();
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const CASES: [PageId, string][] = [
  ["overview", "/"],
  ["needs", "/needs"],
  ["board", "/board"],
  ["tasks", "/tasks"],
  ["task", "/tasks/task-0001"],
  ["activity", "/activity"],
  ["metrics", "/metrics"],
  ["memory", "/memory"],
  ["graphs", "/graphs"],
  ["harness", "/harness"],
  ["handoffs", "/handoffs"],
  ["handoff", "/handoffs/task-0001"],
];

describe("router", () => {
  it("covers all twelve page ids", () => {
    expect(CASES.map(([id]) => id).sort()).toEqual([...PAGE_IDS].sort());
    expect(PAGE_IDS).toHaveLength(12);
  });

  it.each(CASES)("renders the %s page at %s inside the shell", async (id, path) => {
    await renderAt(path);

    expect(await screen.findByRole("heading", { level: 1, name: `stub page ${id}` })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
  });

  it.each(["/nope", "/tasks/task-0001/extra", "/handoffs/a/b"])("renders the 404 page for %s", async (path) => {
    await renderAt(path);

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByText(/stub page/)).not.toBeInTheDocument();
  });

  it("offers a way back from the 404 page", async () => {
    await renderAt("/nope");

    expect(await screen.findByRole("link", { name: "Back to Overview" })).toHaveAttribute("href", "/");
  });

  it("passes the route parameters to the page", async () => {
    const router = await renderAt("/tasks/task-0140");

    expect(router.state.matches.at(-1)?.params).toEqual({ taskId: "task-0140" });
  });

  it("builds the tree from feature modules in a stable order", () => {
    const calls: string[] = [];
    const named = (name: string): FeatureRoutes => ({
      createRoutes: () => {
        calls.push(name);
        return [];
      },
    });

    buildRouteTree({ "../features/zeta/routes.tsx": named("zeta"), "../features/alpha/routes.tsx": named("alpha") });

    expect(calls).toEqual(["alpha", "zeta"]);
  });

  it("collects the real feature modules through the glob", () => {
    for (const [file, module] of Object.entries(featureModules)) {
      expect(file).toMatch(/^\.\.\/features\/[^/]+\/routes\.tsx$/);
      expect(typeof module.createRoutes).toBe("function");
    }
  });
});

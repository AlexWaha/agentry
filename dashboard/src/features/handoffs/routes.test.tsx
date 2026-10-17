import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import handoffs from "@/test/fixtures/harness/handoffs.json";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

async function load(url: string) {
  mockApi({ "/api/v1/handoffs": ok(handoffs.body.data, handoffs.body.meta) });
  const router = createAppRouter({ handoffs: routes }, createMemoryHistory({ initialEntries: [url] }));
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("handoffs routes", () => {
  it.each(["abc", "0", "-3"])("drops the invalid page %s from the search and shows page 1", async (page) => {
    const router = await load(`/handoffs?page=${page}`);

    expect(await screen.findByRole("heading", { level: 1, name: "Handoffs" })).toBeInTheDocument();
    expect(await screen.findByText("task-0107")).toBeInTheDocument();
    expect(router.state.location.search.page).toBeUndefined();
  });

  it("registers the reader route next to the list", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": { status: 404, body: { message: "Not found", code: "not_found" } } });
    const router = createAppRouter({ handoffs: routes }, createMemoryHistory({ initialEntries: ["/handoffs/task-0107"] }));
    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Handoff not found" })).toBeInTheDocument();
  });
});

import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import harness from "@/test/fixtures/harness/harness.json";
import status from "@/test/fixtures/harness/status.json";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

async function load(url: string) {
  mockApi({ "/api/v1/harness": ok(harness.body.data), "/api/v1/status": ok(status.body.data) });
  const router = createAppRouter({ harness: routes }, createMemoryHistory({ initialEntries: [url] }));
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("harness routes", () => {
  it("drops an unknown tab from the search, so the raw value does not survive the validator", async () => {
    const router = await load("/harness?tab=bogus");

    expect(await screen.findByRole("tab", { name: "Agents 4", selected: true })).toBeInTheDocument();
    expect(router.state.location.search.tab).toBeUndefined();
  });

  it("keeps a valid tab and lazy-loads the page", async () => {
    const router = await load("/harness?tab=hooks");

    expect(await screen.findByRole("tab", { name: "Hooks 4", selected: true })).toBeInTheDocument();
    expect(router.state.location.search.tab).toBe("hooks");
  });
});

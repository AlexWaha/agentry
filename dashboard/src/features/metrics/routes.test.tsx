import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import metrics from "@/test/fixtures/activity/metrics.json";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("metrics routes", () => {
  it("registers /metrics and drops an invalid range before it reaches the API", async () => {
    const api = mockApi({ "/api/v1/metrics": ok(metrics.body.data) });
    const router = createAppRouter({ metrics: routes }, createMemoryHistory({ initialEntries: ["/metrics?days=45"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Metrics" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Stage dwell" })).toBeInTheDocument();
    expect(api.callsTo("/api/v1/metrics")[0].url.search).toBe("?days=14");
    expect(router.state.location.search).toEqual({});
  });
});

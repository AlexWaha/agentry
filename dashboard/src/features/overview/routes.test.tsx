import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import eventsFixture from "@/test/fixtures/pipeline/events.json";
import runsFixture from "@/test/fixtures/shell/runs.json";
import statusFixture from "@/test/fixtures/shell/status.json";
import { mockApi } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("overview routes", () => {
  it("registers / and loads the page lazily", async () => {
    mockApi({
      "/api/v1/status": fromFixture(statusFixture),
      "/api/v1/runs": fromFixture(runsFixture),
      "/api/v1/events": fromFixture(eventsFixture),
    });
    const router = createAppRouter({ overview: routes }, createMemoryHistory({ initialEntries: ["/"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Current runs" })).toBeInTheDocument();
  });
});

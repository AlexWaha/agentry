import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import runsFixture from "@/test/fixtures/shell/runs.json";
import { mockApi } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("needs routes", () => {
  it("registers /needs and loads the page lazily", async () => {
    mockApi({ "/api/v1/runs": fromFixture(runsFixture) });
    const router = createAppRouter({ needs: routes }, createMemoryHistory({ initialEntries: ["/needs"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Needs you" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Runs waiting for you" })).toBeInTheDocument();
  });
});

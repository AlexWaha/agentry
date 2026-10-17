import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import detailFixture from "@/test/fixtures/pipeline/task-detail.json";
import { mockApi } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("task routes", () => {
  it("registers /tasks/$taskId and reads the id from the path", async () => {
    const api = mockApi({ "/api/v1/tasks/task-0104": fromFixture(detailFixture) });
    const router = createAppRouter({ task: routes }, createMemoryHistory({ initialEntries: ["/tasks/task-0104"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Delta in flight" })).toBeInTheDocument();
    expect(api.callsTo("/api/v1/tasks/task-0104")).toHaveLength(1);
  });
});

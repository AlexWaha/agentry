import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import tasksFixture from "@/test/fixtures/pipeline/tasks-list.json";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("tasks routes", () => {
  it("registers /tasks and drops invalid search params before they reach the API", async () => {
    const api = mockApi({ "/api/v1/tasks": fromFixture(tasksFixture), "/api/v1/epics": ok([]) });
    const router = createAppRouter(
      { tasks: routes },
      createMemoryHistory({ initialEntries: ["/tasks?folder=bogus&page=abc&superseded=0&type=feature"] }),
    );

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Tasks" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Tasks" })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ type: "feature" });
    expect(api.callsTo("/api/v1/tasks")[0].url.searchParams.toString()).toBe("limit=25&offset=0&type=feature");
  });
});

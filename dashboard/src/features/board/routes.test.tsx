import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { tasksAnswer } from "@/test/fixtures/pipeline/builders";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("board routes", () => {
  it("registers /board and drops an invalid superseded flag before it reaches the API", async () => {
    const api = mockApi({ "/api/v1/tasks": tasksAnswer([], 0, { limit: 12 }), "/api/v1/epics": ok([]) });
    const router = createAppRouter({ board: routes }, createMemoryHistory({ initialEntries: ["/board?superseded=maybe&epic=epic-0001"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Board" })).toBeInTheDocument();
    expect(await screen.findAllByText("Nothing here.")).toHaveLength(3);
    expect(router.state.location.search).toEqual({ epic: "epic-0001" });
    for (const request of api.callsTo("/api/v1/tasks")) expect(request.url.searchParams.has("include_superseded")).toBe(false);
  });
});

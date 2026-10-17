import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import events from "@/test/fixtures/activity/events.json";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("activity routes", () => {
  it("registers /activity and drops invalid search params before they reach the API", async () => {
    const api = mockApi({ "/api/v1/events": ok(events.body.data, events.body.meta) });
    const url = "/activity?task=no%20spaces&kind=bogus&result=maybe&page=abc";
    const router = createAppRouter({ activity: routes }, createMemoryHistory({ initialEntries: [url] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Activity" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Journal events" })).toBeInTheDocument();
    expect(api.callsTo("/api/v1/events")[0].url.search).toBe("?limit=25&offset=0");
    expect(router.state.location.search).toEqual({});
  });
});

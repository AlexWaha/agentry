import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import lessons from "@/test/fixtures/memory/lessons.json";
import stats from "@/test/fixtures/memory/stats.json";
import { mockApi, ok } from "@/test/utils/api-mock";

import * as routes from "./routes";

describe("memory routes", () => {
  it("registers /memory and loads the page lazily with its search params validated", async () => {
    mockApi({ "/api/v1/memory/stats": ok(stats.body.data), "/api/v1/memory": ok(lessons.body.data, lessons.body.meta) });
    const router = createAppRouter({ memory: routes }, createMemoryHistory({ initialEntries: ["/memory?tab=bogus&page=abc"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Memory" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "lessons" })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({});
  });
});

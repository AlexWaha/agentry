import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { mockApi, ok } from "@/test/utils/api-mock";
import { installCanvasStub } from "@/test/utils/canvas";

import * as routes from "./routes";
import { validateGraphsSearch } from "./search";

describe("graphs routes", () => {
  it("registers /graphs and loads the page lazily", async () => {
    const stub = installCanvasStub();
    mockApi({ "/api/v1/graphs/memory": ok(memoryGraph.body.data) });
    const router = createAppRouter({ graphs: routes }, createMemoryHistory({ initialEntries: ["/graphs?view=bogus"] }));

    render(
      <QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Graphs" })).toBeInTheDocument();
    expect(await screen.findByRole("application", { name: /Memory graph/ })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({});
    stub.restore();
  });
});

describe("validateGraphsSearch", () => {
  it("omits the default view and keeps the index only for the code view", () => {
    expect(validateGraphsSearch({ view: "memory", index: "root" })).toEqual({});
    expect(validateGraphsSearch({ view: "code", index: "root" })).toEqual({ view: "code", index: "root" });
    expect(validateGraphsSearch({ view: "code" })).toEqual({ view: "code" });
    expect(validateGraphsSearch({ view: "nope", index: "root" })).toEqual({});
  });
});

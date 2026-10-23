import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ROUTES } from "@/config/routes";
import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { installCanvasStub, type CanvasStub } from "@/test/utils/canvas";
import { renderWithApp } from "@/test/utils/render";

import { GraphPanel } from "./GraphPanel";
import { validateGraphsSearch } from "./search";

/*
 * The 3D chunk (three.js) cannot be downloaded: importing it rejects. The panel must say that, as something other
 * than "no WebGL", and show the 2D view. This lives in its own file because the mock replaces the module for every
 * test of the file.
 */
vi.mock("./graph3d", () => {
  throw new Error("Failed to fetch dynamically imported module");
});

const GRAPH = memoryGraph.body.data;
let stub: CanvasStub;

beforeEach(() => {
  stub = installCanvasStub();
});

afterEach(() => {
  stub.restore();
});

describe("GraphPanel when the 3D chunk fails to load", () => {
  it("says the 3D code could not be loaded, not that WebGL is missing, and shows the 2D view", async () => {
    await renderWithApp(<GraphPanel title="Memory graph" nodes={GRAPH.nodes} edges={GRAPH.edges} />, {
      url: "/graphs?mode=3d",
      path: ROUTES.graphs,
      validateSearch: validateGraphsSearch,
    });

    expect(await screen.findByText(/The 3D view could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByText(/WebGL could not start/)).not.toBeInTheDocument();
    expect((await screen.findByRole("application", { name: /Memory graph/ })).tagName).toBe("CANVAS");
    expect(screen.getByRole("button", { name: "2D" })).toHaveAttribute("aria-pressed", "true");
  });
});

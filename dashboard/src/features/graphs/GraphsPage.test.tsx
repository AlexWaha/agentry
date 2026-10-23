import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ROUTES } from "@/config/routes";
import { bytes } from "@/lib/format";
import codegraph from "@/test/fixtures/memory/codegraph.json";
import { codeGraph, codeGraphMeta } from "@/test/fixtures/memory/codegraph-graph";
import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok, type MockResponse } from "@/test/utils/api-mock";
import { installCanvasStub, type CanvasStub } from "@/test/utils/canvas";
import { setReducedMotion } from "@/test/utils/match-media";
import { renderWithApp } from "@/test/utils/render";

import { createGraph3D } from "./graph3d";
import { GraphsPage } from "./GraphsPage";
import { validateGraphsSearch } from "./search";

vi.mock("./graph3d", () => ({
  createGraph3D: vi.fn(() => ({
    fit: vi.fn(),
    reset: vi.fn(),
    zoomBy: vi.fn(),
    setView: vi.fn(),
    move: vi.fn(() => null),
    reveal: vi.fn(),
    destroy: vi.fn(),
  })),
}));

const MEMORY_GRAPH = memoryGraph.body.data;
const NEVER = (): Promise<Response> => new Promise<Response>(() => undefined);

function renderGraphs(url = "/graphs") {
  return renderWithApp(<GraphsPage />, { url, path: ROUTES.graphs, validateSearch: validateGraphsSearch });
}

const codeRoutes = (graph: MockResponse = ok(codeGraph, codeGraphMeta)) => ({
  "/api/v1/codegraph": ok(codegraph.body.data),
  "/api/v1/codegraph/root/graph": graph,
});

let stub: CanvasStub;

beforeEach(() => {
  stub = installCanvasStub();
  setReducedMotion(true);
});

afterEach(() => {
  stub.restore();
});

const drawing = (name: RegExp) => screen.findByRole("application", { name });
const announced = (): string => document.querySelector('[aria-live="polite"].sr-only')?.textContent ?? "";
const arcCalls = (): number => stub.calls.filter((call) => call.name === "arc").length;

describe("Graphs page, memory graph states", () => {
  it("shows a loading status while the request is pending", async () => {
    mockApi().fetch.mockImplementation(NEVER);

    await renderGraphs();

    expect(screen.getByRole("status", { name: "Loading graph" })).toBeInTheDocument();
  });

  it("explains an empty graph", async () => {
    mockApi({ "/api/v1/graphs/memory": ok({ nodes: [], edges: [] }) });

    await renderGraphs();

    expect(await screen.findByText("The memory graph is empty.")).toBeInTheDocument();
  });

  it("shows the error with a retry that draws the graph", async () => {
    let calls = 0;
    mockApi({ "/api/v1/graphs/memory": () => (++calls === 1 ? fail(500, "internal", "Graph failed") : ok(MEMORY_GRAPH)) });
    await renderGraphs();

    expect(await screen.findByRole("alert")).toHaveTextContent("Graph failed");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await drawing(/Memory graph, 9 nodes and 7 edges/)).toBeInTheDocument();
  });

  it("draws the graph and has no axe violation in the data state", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();

    await drawing(/Memory graph, 9 nodes and 7 edges/);

    await expectNoAxeViolations(container);
  });
});

describe("Graphs page, memory graph drawing", () => {
  it("paints every fixture node on the canvas and shows one legend entry per kind", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();
    await drawing(/Memory graph/);

    await waitFor(() => expect(arcCalls()).toBeGreaterThanOrEqual(MEMORY_GRAPH.nodes.length));
    const kinds = [...new Set(MEMORY_GRAPH.nodes.map((node) => node.kind))];
    const legend = within(screen.getByRole("list", { name: "Legend" })).getAllByRole("listitem");
    expect(legend.map((item) => item.textContent)).toEqual(expect.arrayContaining(["Lesson", "Pattern", "Task", "Agent", "Area"]));
    expect(legend).toHaveLength(kinds.length);
  });

  it("is one tab stop whose PageDown, Home and End keys reach every node and announce it", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();
    const canvas = await drawing(/Memory graph/);
    const user = userEvent.setup();
    const reached = new Set<string>();

    expect(document.querySelectorAll('canvas[tabindex="0"]')).toHaveLength(1);
    act(() => canvas.focus());
    reached.add(announced());
    for (let step = 1; step < MEMORY_GRAPH.nodes.length; step += 1) {
      await user.keyboard("{PageDown}");
      reached.add(announced());
    }

    expect(reached.size).toBe(MEMORY_GRAPH.nodes.length);
    await user.keyboard("{Home}");
    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
    await user.keyboard("{End}");
    expect(announced()).toBe("Pattern: pattern-two, 0 links");
    expect(document.activeElement).toBe(canvas);
  });

  it("draws the most linked nodes of a huge graph together with their neighbours, says so, and pages the table", async () => {
    const nodes = Array.from({ length: 450 }, (_, index) => ({ id: `n${index}`, kind: "lesson", label: `n${index}`, weight: index }));
    const edges = [
      { source: "n449", target: "n448", kind: "x", weight: 1 },
      { source: "n0", target: "n449", kind: "x", weight: 1 },
    ];
    mockApi({ "/api/v1/graphs/memory": ok({ nodes, edges }) });
    await renderGraphs();
    await drawing(/Memory graph, 400 nodes and 2 edges/);

    expect(screen.getByRole("status")).toHaveTextContent("Showing 400 of 450 nodes: the most linked ones and the nodes that connect them");

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(101);
    expect(screen.getByRole("status")).toHaveTextContent("1-100 of 450 nodes");
    await userEvent.click(screen.getByRole("button", { name: "Page 5" }));
    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(51);
  });

  it("shows the details of the node Enter is pressed on and clears them on a second Enter", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();
    const canvas = await drawing(/Memory graph/);

    act(() => canvas.focus());
    await userEvent.keyboard("{Enter}");

    const details = within(screen.getByRole("region", { name: "Selected node" }));
    expect(details.getByText("alpha-lesson")).toBeInTheDocument();
    expect(details.getByText("3 links")).toBeInTheDocument();

    await userEvent.keyboard("{Enter}");

    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
  });

  it("offers Fit to view and Reset view next to the drawing and not next to the table", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();
    await drawing(/Memory graph/);
    expect(screen.getByRole("button", { name: "Fit to view" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset view" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    expect(screen.queryByRole("button", { name: "Fit to view" })).not.toBeInTheDocument();
  });

  it("lists every node and edge in Show as table and goes back to the graph", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();
    await drawing(/Memory graph/);

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    const nodeTable = screen.getByRole("table", { name: "Nodes" });
    const edgeTable = screen.getByRole("table", { name: "Edges" });
    expect(within(nodeTable).getAllByRole("row")).toHaveLength(MEMORY_GRAPH.nodes.length + 1);
    expect(within(edgeTable).getAllByRole("row")).toHaveLength(MEMORY_GRAPH.edges.length + 1);
    for (const node of MEMORY_GRAPH.nodes) expect(within(nodeTable).getByText(node.label)).toBeInTheDocument();
    const firstEdge = within(edgeTable).getAllByRole("row")[1];
    expect(firstEdge).toHaveTextContent("alpha-lesson");
    expect(firstEdge).toHaveTextContent("task-0106");
    expect(screen.queryByRole("application")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show as graph" }));

    expect(await drawing(/Memory graph/)).toBeInTheDocument();
  });
});

describe("Graphs page, 2D and 3D mode", () => {
  const modeButton = (name: "2D" | "3D") => screen.getByRole("button", { name });

  it("defaults to 2D when the OS asks for reduced motion and starts no 3D scene", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();

    const view = await drawing(/Memory graph/);

    expect(view.tagName).toBe("CANVAS");
    expect(modeButton("2D")).toHaveAttribute("aria-pressed", "true");
    expect(modeButton("3D")).toHaveAttribute("aria-pressed", "false");
    expect(createGraph3D).not.toHaveBeenCalled();
  });

  it("defaults to 3D without reduced motion and loads the 3D scene", async () => {
    setReducedMotion(false);
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs();

    const view = await drawing(/Memory graph/);

    expect(view).toHaveAttribute("data-mode", "3d");
    expect(modeButton("3D")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalledTimes(1));
  });

  it("shows 3D for ?mode=3d even under reduced motion, and 2D for ?mode=2d without it", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs("/graphs?mode=3d");
    expect(await drawing(/Memory graph/)).toHaveAttribute("data-mode", "3d");
  });

  it("keeps 2D for ?mode=2d when motion is not reduced", async () => {
    setReducedMotion(false);
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs("/graphs?mode=2d");

    expect((await drawing(/Memory graph/)).tagName).toBe("CANVAS");
    expect(createGraph3D).not.toHaveBeenCalled();
  });

  it("switches mode with the buttons and records the choice in the URL", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { router } = await renderGraphs();
    await drawing(/Memory graph/);

    await userEvent.click(modeButton("3D"));

    expect(router.state.location.search).toEqual({ mode: "3d" });
    expect(await drawing(/Memory graph/)).toHaveAttribute("data-mode", "3d");
    await userEvent.click(modeButton("2D"));

    expect(router.state.location.search).toEqual({ mode: "2d" });
    expect((await drawing(/Memory graph/)).tagName).toBe("CANVAS");
  });

  it("drops an unknown mode from the URL and keeps the default", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { router } = await renderGraphs("/graphs?mode=4d");

    expect((await drawing(/Memory graph/)).tagName).toBe("CANVAS");
    expect(router.state.location.search).toEqual({});
  });

  it("keeps the chosen mode when the view changes to the code graph", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH), ...codeRoutes() });
    const { router } = await renderGraphs("/graphs?mode=3d");
    await drawing(/Memory graph/);

    await userEvent.click(screen.getByRole("tab", { name: "Code graph" }));

    expect(await drawing(/Modules of root/)).toHaveAttribute("data-mode", "3d");
    expect(router.state.location.search).toEqual({ view: "code", mode: "3d" });
  });

  it("falls back to 2D with a notice when the 3D scene cannot start, and tries 3D again on request", async () => {
    vi.mocked(createGraph3D).mockImplementationOnce(() => {
      throw new Error("Error creating WebGL context");
    });
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs("/graphs?mode=3d");

    expect(await screen.findByText(/The 3D view is not available in this browser/)).toBeInTheDocument();
    expect((await drawing(/Memory graph/)).tagName).toBe("CANVAS");
    expect(modeButton("2D")).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(modeButton("3D"));

    expect(await drawing(/Memory graph/)).toHaveAttribute("data-mode", "3d");
    expect(screen.queryByText(/The 3D view is not available in this browser/)).not.toBeInTheDocument();
  });

  it("lists nodes and edges in Show as table whichever mode is chosen", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    await renderGraphs("/graphs?mode=3d");
    await drawing(/Memory graph/);

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(MEMORY_GRAPH.nodes.length + 1);
  });

  it("has no axe violation with the mode switch in both modes", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs("/graphs?mode=3d");
    await drawing(/Memory graph/);
    await expectNoAxeViolations(container);

    await userEvent.click(modeButton("2D"));
    await drawing(/Memory graph/);

    await expectNoAxeViolations(container);
  });
});

describe("Graphs page, code graph", () => {
  it("switches the view in the URL", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH), ...codeRoutes() });
    const { router } = await renderGraphs();

    await userEvent.click(screen.getByRole("tab", { name: "Code graph" }));

    expect(await screen.findByRole("heading", { name: "root" })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ view: "code" });
  });

  it("lists each index with size, last sync and counts, and draws the supported one", async () => {
    const api = mockApi(codeRoutes());
    await renderGraphs("/graphs?view=code");

    const root = (await screen.findByRole("heading", { name: "root" })).closest("li") as HTMLElement;

    expect(root).toHaveTextContent(`Size: ${bytes(2097152)}`);
    expect(root).toHaveTextContent("Last sync: 10-16 08:15:00Z");
    expect(root).toHaveTextContent("Files: 120");
    expect(root).toHaveTextContent("Symbols: 940");
    expect(root).toHaveTextContent("Edges: 2310");
    expect(screen.getByRole("heading", { name: "agentry" }).closest("li")).toHaveTextContent("Unsupported schema");
    await drawing(/Modules of root, 4 nodes and 4 edges/);
    expect(screen.queryByText(/truncated/)).not.toBeInTheDocument();
    expect(api.callsTo("/api/v1/codegraph/root/graph")[0].url.searchParams.get("limit")).toBe("400");
  });

  it("shows the truncated notice when meta.truncated is true", async () => {
    mockApi(codeRoutes(ok(codeGraph, { ...codeGraphMeta, truncated: true })));
    await renderGraphs("/graphs?view=code");

    expect(await screen.findByText(/The graph is truncated/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the unavailable state, not an error, when no index exists", async () => {
    mockApi({ "/api/v1/codegraph": ok([]) });
    await renderGraphs("/graphs?view=code");

    expect(await screen.findByText("No code graph available.")).toBeInTheDocument();
    expect(screen.getByText(/no \.codegraph index/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the unavailable state, not an error, for an index that is not supported", async () => {
    const api = mockApi(codeRoutes());
    const { router } = await renderGraphs("/graphs?view=code&index=agentry");

    expect(await screen.findByText("No code graph available.")).toBeInTheDocument();
    expect(screen.getByText(/no table layout the dashboard can read/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.callsTo("/api/v1/codegraph/agentry/graph")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Show graph: root" }));

    expect(await drawing(/Modules of root/)).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ view: "code", index: "root" });
  });

  it("shows the unavailable state when the selected index has no modules", async () => {
    mockApi(codeRoutes(ok({ nodes: [], edges: [] }, codeGraphMeta)));
    await renderGraphs("/graphs?view=code");

    expect(await screen.findByText(/returned no modules/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading status for the index list", async () => {
    mockApi().fetch.mockImplementation(NEVER);

    await renderGraphs("/graphs?view=code");

    expect(screen.getByRole("status", { name: "Loading graph" })).toBeInTheDocument();
  });

  it("shows an error with retry when the index list fails", async () => {
    mockApi({ "/api/v1/codegraph": fail(500, "internal", "Index list failed") });
    await renderGraphs("/graphs?view=code");

    expect(await screen.findByRole("alert")).toHaveTextContent("Index list failed");
  });

  it("shows an error when the graph request of a supported index fails", async () => {
    mockApi(codeRoutes(fail(500, "internal", "Graph query failed")));
    await renderGraphs("/graphs?view=code");

    expect(await screen.findByRole("alert")).toHaveTextContent("Graph query failed");
  });

  it("has no axe violation in the data state", async () => {
    mockApi(codeRoutes());
    const { container } = await renderGraphs("/graphs?view=code");
    await drawing(/Modules of root/);

    await expectNoAxeViolations(container);
  });
});

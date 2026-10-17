import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ROUTES } from "@/config/routes";
import { bytes } from "@/lib/format";
import codegraph from "@/test/fixtures/memory/codegraph.json";
import { codeGraph, codeGraphMeta } from "@/test/fixtures/memory/codegraph-graph";
import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok, type MockResponse } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";

import { GraphsPage } from "./GraphsPage";
import { validateGraphsSearch } from "./search";

const MEMORY_GRAPH = memoryGraph.body.data;
const NEVER = (): Promise<Response> => new Promise<Response>(() => undefined);

function renderGraphs(url = "/graphs") {
  return renderWithApp(<GraphsPage />, { url, path: ROUTES.graphs, validateSearch: validateGraphsSearch });
}

const codeRoutes = (graph: MockResponse = ok(codeGraph, codeGraphMeta)) => ({
  "/api/v1/codegraph": ok(codegraph.body.data),
  "/api/v1/codegraph/root/graph": graph,
});

const nodesOf = (container: HTMLElement): Element[] => [...container.querySelectorAll("svg [data-kind]")];
const stateOf = (container: HTMLElement, label: string): string | null =>
  nodesOf(container).find((node) => node.getAttribute("aria-label")?.includes(`: ${label},`))?.getAttribute("data-state") ?? null;

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
    const { container } = await renderGraphs();

    expect(await screen.findByRole("alert")).toHaveTextContent("Graph failed");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(nodesOf(container)).toHaveLength(MEMORY_GRAPH.nodes.length));
  });

  it("draws the graph and has no axe violation in the data state", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();

    await screen.findByRole("group", { name: /Memory graph, 9 nodes and 7 edges/ });

    await expectNoAxeViolations(container);
  });
});

describe("Graphs page, memory graph drawing", () => {
  it("draws one node per fixture node, colored by kind, and one legend entry per kind", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });

    const nodes = nodesOf(container);
    expect(nodes).toHaveLength(MEMORY_GRAPH.nodes.length);
    const fillOf = (kind: string): string | undefined =>
      nodes.find((node) => node.getAttribute("data-kind") === kind)?.querySelector("circle")?.getAttribute("class") ?? undefined;
    const kinds = [...new Set(MEMORY_GRAPH.nodes.map((node) => node.kind))];
    expect(new Set(kinds.map(fillOf)).size).toBe(kinds.length);
    expect(fillOf("lesson")).toContain("fill-status-active-fg");
    expect(fillOf("pattern")).toContain("fill-status-done-fg");
    const legend = within(screen.getByRole("list", { name: "Legend" })).getAllByRole("listitem");
    expect(legend.map((item) => item.textContent)).toEqual(expect.arrayContaining(["Lesson", "Pattern", "Task", "Agent", "Area"]));
    expect(legend).toHaveLength(kinds.length);
  });

  it("is one tab stop, reaches every node with the arrow keys and shows only the focused node's label", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });
    const user = userEvent.setup();
    const reached = new Set<string>();
    const remember = (): void => {
      const focused = document.activeElement;
      expect(focused?.hasAttribute("data-kind")).toBe(true);
      reached.add(focused?.getAttribute("aria-label") ?? "");
      const labels = [...container.querySelectorAll("svg text")].map((text) => text.textContent);
      expect(labels).toHaveLength(1);
      expect(focused?.getAttribute("aria-label")).toContain(`: ${labels[0]},`);
    };

    expect(container.querySelector("svg text")).toBeNull();
    expect(nodesOf(container).filter((node) => node.getAttribute("tabindex") === "0")).toHaveLength(1);
    while (!document.activeElement?.hasAttribute("data-kind")) await user.tab();
    remember();
    for (let step = 1; step < MEMORY_GRAPH.nodes.length; step += 1) {
      await user.keyboard("{ArrowRight}");
      remember();
    }
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(nodesOf(container)[0]);
    expect(nodesOf(container).filter((node) => node.getAttribute("tabindex") === "0")).toHaveLength(1);
    await user.keyboard("{End}");

    expect(document.activeElement).toBe(nodesOf(container)[MEMORY_GRAPH.nodes.length - 1]);
    expect(reached.size).toBe(MEMORY_GRAPH.nodes.length);
  });

  it("draws only the heaviest nodes of a huge graph, says so, and pages the table", async () => {
    const nodes = Array.from({ length: 450 }, (_, index) => ({ id: `n${index}`, kind: "lesson", label: `n${index}`, weight: index }));
    const edges = [
      { source: "n449", target: "n448", kind: "x", weight: 1 },
      { source: "n0", target: "n449", kind: "x", weight: 1 },
    ];
    mockApi({ "/api/v1/graphs/memory": ok({ nodes, edges }) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph, 400 nodes and 1 edges/ });

    expect(nodesOf(container)).toHaveLength(400);
    expect(screen.getByRole("status")).toHaveTextContent("Showing the 400 heaviest of 450 nodes");
    expect(nodesOf(container).some((node) => node.getAttribute("data-id") === "n0")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(101);
    expect(screen.getByRole("status")).toHaveTextContent("1-100 of 450 nodes");
    await userEvent.click(screen.getByRole("button", { name: "Page 5" }));
    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(51);
  });

  it("shows a node's label on hover and removes it on leave", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });
    const node = nodesOf(container)[0];

    await userEvent.hover(node);
    expect(container.querySelectorAll("svg text")).toHaveLength(1);
    await userEvent.unhover(node);

    expect(container.querySelector("svg text")).toBeNull();
  });

  it("highlights the neighbours of the node Enter is pressed on, dims the rest, and clears on a second Enter", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });
    const alpha = nodesOf(container).find((node) => node.getAttribute("aria-label")?.includes("alpha-lesson")) as SVGGElement;

    alpha.focus();
    await userEvent.keyboard("{Enter}");

    expect(alpha).toHaveAttribute("aria-pressed", "true");
    expect(stateOf(container, "alpha-lesson")).toBe("selected");
    for (const neighbour of ["task-0106", "senior-backend-dev", "hooks"]) expect(stateOf(container, neighbour)).toBe("neighbor");
    for (const other of ["beta-lesson", "qa-engineer", "task-0107", "pattern-two"]) expect(stateOf(container, other)).toBe("dimmed");
    expect([...container.querySelectorAll("svg text")].map((text) => text.textContent).sort()).toEqual(
      ["alpha-lesson", "hooks", "senior-backend-dev", "task-0106"],
    );

    await userEvent.keyboard("{Enter}");

    expect(nodesOf(container).every((node) => node.getAttribute("data-state") === "idle")).toBe(true);
  });

  it("selects a node with a click", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });
    const hooks = nodesOf(container).find((node) => node.getAttribute("aria-label")?.includes("hooks")) as SVGGElement;

    fireEvent.click(hooks);

    expect(stateOf(container, "hooks")).toBe("selected");
    expect(stateOf(container, "alpha-lesson")).toBe("neighbor");
    expect(stateOf(container, "beta-lesson")).toBe("neighbor");
  });

  it("lists every node and edge in Show as table and goes back to the graph", async () => {
    mockApi({ "/api/v1/graphs/memory": ok(MEMORY_GRAPH) });
    const { container } = await renderGraphs();
    await screen.findByRole("group", { name: /Memory graph/ });

    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));

    const nodeTable = screen.getByRole("table", { name: "Nodes" });
    const edgeTable = screen.getByRole("table", { name: "Edges" });
    expect(within(nodeTable).getAllByRole("row")).toHaveLength(MEMORY_GRAPH.nodes.length + 1);
    expect(within(edgeTable).getAllByRole("row")).toHaveLength(MEMORY_GRAPH.edges.length + 1);
    for (const node of MEMORY_GRAPH.nodes) expect(within(nodeTable).getByText(node.label)).toBeInTheDocument();
    const firstEdge = within(edgeTable).getAllByRole("row")[1];
    expect(firstEdge).toHaveTextContent("alpha-lesson");
    expect(firstEdge).toHaveTextContent("task-0106");
    expect(container.querySelector("svg [data-kind]")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Show as graph" }));

    expect(nodesOf(container)).toHaveLength(MEMORY_GRAPH.nodes.length);
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
    mockApi(codeRoutes());
    const { container } = await renderGraphs("/graphs?view=code");

    const root = (await screen.findByRole("heading", { name: "root" })).closest("li") as HTMLElement;

    expect(root).toHaveTextContent(`Size: ${bytes(2097152)}`);
    expect(root).toHaveTextContent("Last sync: 10-16 08:15:00Z");
    expect(root).toHaveTextContent("Files: 120");
    expect(root).toHaveTextContent("Symbols: 940");
    expect(root).toHaveTextContent("Edges: 2310");
    expect(screen.getByRole("heading", { name: "agentry" }).closest("li")).toHaveTextContent("Unsupported schema");
    await screen.findByRole("group", { name: /Modules of root, 4 nodes and 4 edges/ });
    expect(nodesOf(container)).toHaveLength(codeGraph.nodes.length);
    expect(screen.queryByText(/truncated/)).not.toBeInTheDocument();
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

    expect(await screen.findByRole("group", { name: /Modules of root/ })).toBeInTheDocument();
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
    await screen.findByRole("group", { name: /Modules of root/ });

    await expectNoAxeViolations(container);
  });
});

import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "@/types/api";

import { LABEL_TOP_HUBS } from "./config";
import { buildScene, capGraph, createSimulation, radiusOf, settle } from "./layout";

const node = (id: string, weight = 1): GraphNode => ({ id, kind: "lesson", label: id, weight });
const edge = (source: string, target: string): GraphEdge => ({ source, target, kind: "task", weight: 1 });

/** A hub with `spokes` leaves, each leaf with one more node behind it: 1 + 2 * spokes nodes, all connected. */
function star(spokes: number): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes = [node("hub", spokes)];
  const edges: GraphEdge[] = [];
  for (let i = 0; i < spokes; i += 1) {
    nodes.push(node(`leaf${i}`, 2), node(`tail${i}`, 1));
    edges.push(edge("hub", `leaf${i}`), edge(`leaf${i}`, `tail${i}`));
  }
  return { nodes, edges };
}

/** True when every node can reach every other along the edges. */
function isConnected(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): boolean {
  if (nodes.length === 0) return true;
  const adjacent = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    adjacent.get(e.source)?.push(e.target);
    adjacent.get(e.target)?.push(e.source);
  }
  const seen = new Set([nodes[0].id]);
  const queue = [nodes[0].id];
  while (queue.length > 0) {
    for (const next of adjacent.get(queue.shift() as string) ?? []) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen.size === nodes.length;
}

describe("capGraph", () => {
  it("returns a graph that already fits untouched", () => {
    const { nodes, edges } = star(3);

    const capped = capGraph(nodes, edges, 100);

    expect(capped.nodes).toBe(nodes);
    expect(capped.edges).toBe(edges);
  });

  it("keeps at most the limit and only the edges between kept nodes", () => {
    const { nodes, edges } = star(30);

    const capped = capGraph(nodes, edges, 20);

    expect(capped.nodes).toHaveLength(20);
    const ids = new Set(capped.nodes.map((n) => n.id));
    expect(capped.edges.length).toBeGreaterThan(0);
    for (const e of capped.edges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
    }
  });

  it("keeps a connected graph connected instead of the heaviest nodes in isolation", () => {
    const { nodes, edges } = star(30);

    const capped = capGraph(nodes, edges, 20);

    expect(isConnected(capped.nodes, capped.edges)).toBe(true);
    expect(capped.nodes.some((n) => n.id === "hub")).toBe(true);
  });

  it("gives every kept non-seed node a link to a kept node", () => {
    // 40 isolated heavy nodes would win a plain top-N; each light node here hangs off a heavy one.
    const heavy = Array.from({ length: 10 }, (_, i) => node(`heavy${i}`, 100 - i));
    const light = Array.from({ length: 30 }, (_, i) => node(`light${i}`, 1));
    const edges = light.map((n, i) => edge(`heavy${i % 10}`, n.id));

    const capped = capGraph([...heavy, ...light], edges, 20);

    const linked = new Set(capped.edges.flatMap((e) => [e.source, e.target]));
    expect(capped.nodes.every((n) => linked.has(n.id))).toBe(true);
  });

  it("is deterministic", () => {
    const { nodes, edges } = star(30);

    expect(capGraph(nodes, edges, 20)).toEqual(capGraph(nodes, edges, 20));
  });

  it("fills the budget with whole components when seeds have no more neighbours", () => {
    const isolated = Array.from({ length: 30 }, (_, i) => node(`alone${i}`, 0));
    const pairA = [node("a1", 1), node("a2", 1)];
    const pairB = [node("b1", 1), node("b2", 1)];

    const capped = capGraph([...isolated, ...pairA, ...pairB], [edge("a1", "a2"), edge("b1", "b2")], 10);

    expect(capped.nodes).toHaveLength(10);
    expect(capped.edges).toHaveLength(2);
  });

  it("ignores an edge that points at an unknown node and a self edge", () => {
    const { nodes, edges } = star(30);

    expect(() => capGraph(nodes, [...edges, edge("hub", "ghost"), edge("hub", "hub")], 20)).not.toThrow();
  });
});

describe("radiusOf", () => {
  it("grows with the weight and stops at a maximum", () => {
    expect(radiusOf(0)).toBeLessThan(radiusOf(4));
    expect(radiusOf(4)).toBeLessThan(radiusOf(9));
    expect(radiusOf(100000)).toBe(radiusOf(200000));
  });
});

describe("buildScene", () => {
  const nodes = [node("a", 3), node("b"), node("c"), node("lonely", 0)];
  const edges = [edge("a", "b"), edge("a", "c"), edge("a", "ghost")];

  it("counts the degree of the drawn edges and ignores an edge to an unknown node", () => {
    const scene = buildScene(nodes, edges);

    expect(scene.links).toHaveLength(2);
    expect(scene.byId.get("a")?.degree).toBe(2);
    expect(scene.byId.get("lonely")?.degree).toBe(0);
    expect([...(scene.neighbours.get("a") ?? [])].sort()).toEqual(["b", "c"]);
    expect(scene.neighbours.get("lonely")?.size).toBe(0);
  });

  it("orders nodes by degree, then by label, for the keyboard walk", () => {
    const scene = buildScene(nodes, edges);

    expect(scene.ordered.map((n) => n.id)).toEqual(["a", "b", "c", "lonely"]);
  });

  it("marks the most linked nodes as hubs, never an unlinked one, and at most the hub count", () => {
    const { nodes: many, edges: manyEdges } = star(20);

    const scene = buildScene(many, manyEdges);

    expect(scene.hubs.size).toBe(LABEL_TOP_HUBS);
    expect(scene.hubs.has("hub")).toBe(true);
    expect(buildScene(nodes, edges).hubs.has("lonely")).toBe(false);
  });

  it("builds an empty scene", () => {
    const scene = buildScene([], []);

    expect(scene.nodes).toHaveLength(0);
    expect(scene.ordered).toHaveLength(0);
  });
});

describe("createSimulation and settle", () => {
  const nodes = [node("a", 3), node("b"), node("c"), node("lonely", 0)];
  const edges = [edge("a", "b"), edge("a", "c")];

  const placed = (): { id: string; x: number; y: number }[] => {
    const scene = buildScene(nodes, edges);
    settle(createSimulation(scene));
    return scene.nodes.map((n) => ({ id: n.id, x: n.x ?? NaN, y: n.y ?? NaN }));
  };

  it("settles every node onto a finite position", () => {
    for (const spot of placed()) {
      expect(Number.isFinite(spot.x)).toBe(true);
      expect(Number.isFinite(spot.y)).toBe(true);
    }
  });

  it("lays the same graph out the same way twice", () => {
    expect(placed()).toEqual(placed());
  });

  it("keeps linked nodes closer together than an unlinked one", () => {
    const spots = new Map(placed().map((spot) => [spot.id, spot]));
    const gap = (one: string, two: string): number =>
      Math.hypot((spots.get(one)?.x ?? 0) - (spots.get(two)?.x ?? 0), (spots.get(one)?.y ?? 0) - (spots.get(two)?.y ?? 0));

    expect(gap("a", "b")).toBeLessThan(120);
    expect(gap("a", "b")).toBeLessThan(gap("a", "lonely") + 1);
  });

  it("starts stopped, so the caller owns the clock", () => {
    const simulation = createSimulation(buildScene(nodes, edges));
    const before = simulation.alpha();

    simulation.tick();

    expect(simulation.alpha()).toBeLessThan(before);
  });

  it("cools to rest", () => {
    const simulation = createSimulation(buildScene(nodes, edges));

    settle(simulation);

    expect(simulation.alpha()).toBeLessThan(simulation.alphaMin() * 1.01);
  });

  it("lays out an empty graph", () => {
    expect(() => settle(createSimulation(buildScene([], [])))).not.toThrow();
  });
});

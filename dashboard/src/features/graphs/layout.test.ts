import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "@/types/api";

import { GRAPH_HEIGHT, GRAPH_WIDTH, layoutGraph, radiusOf } from "./layout";

const node = (id: string, weight = 1): GraphNode => ({ id, kind: "lesson", label: id, weight });
const edge = (source: string, target: string): GraphEdge => ({ source, target, kind: "task", weight: 1 });

describe("layoutGraph", () => {
  const nodes = [node("a", 3), node("b"), node("c"), node("lonely", 0)];
  const edges = [edge("a", "b"), edge("a", "c")];

  it("places every node inside the drawing", () => {
    const placed = layoutGraph(nodes, edges);

    expect([...placed.keys()]).toEqual(["a", "b", "c", "lonely"]);
    for (const spot of placed.values()) {
      expect(spot.x).toBeGreaterThan(0);
      expect(spot.x).toBeLessThan(GRAPH_WIDTH);
      expect(spot.y).toBeGreaterThan(0);
      expect(spot.y).toBeLessThan(GRAPH_HEIGHT);
    }
  });

  it("lays the same graph out the same way twice", () => {
    expect(layoutGraph(nodes, edges)).toEqual(layoutGraph(nodes, edges));
  });

  it("keeps linked nodes closer together than the drawing is wide", () => {
    const placed = layoutGraph(nodes, edges);
    const a = placed.get("a");
    const b = placed.get("b");

    expect(Math.hypot((a?.x ?? 0) - (b?.x ?? 0), (a?.y ?? 0) - (b?.y ?? 0))).toBeLessThan(GRAPH_WIDTH / 2);
  });

  it("ignores an edge that points at an unknown node", () => {
    expect(() => layoutGraph(nodes, [...edges, edge("a", "ghost")])).not.toThrow();
  });

  it("lays out an empty graph", () => {
    expect(layoutGraph([], []).size).toBe(0);
  });
});

describe("radiusOf", () => {
  it("grows with the weight and stops at a maximum", () => {
    expect(radiusOf(0)).toBeLessThan(radiusOf(4));
    expect(radiusOf(4)).toBeLessThan(radiusOf(9));
    expect(radiusOf(100000)).toBe(radiusOf(200000));
  });
});

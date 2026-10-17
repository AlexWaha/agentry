import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "@/types/api";

import { FIT_MAX_SCALE, LABEL_ZOOM_THRESHOLD, ZOOM_MAX, ZOOM_MIN } from "./config";
import { buildScene, type SimNode } from "./layout";
import { centredTransform, clampScale, fitTransform, labelledNodes, nearestInDirection, nodeAtPoint, toGraph, toScreen } from "./view";

const SIZE = { width: 800, height: 600 };
const placedNode = (id: string, x: number, y: number, r = 8): SimNode => ({ id, kind: "lesson", label: id, degree: 1, r, x, y });

describe("fitTransform", () => {
  it("centres the origin for an empty graph", () => {
    expect(fitTransform([], SIZE)).toEqual(centredTransform(SIZE));
  });

  it("puts every node and its circle inside the canvas, centred", () => {
    const nodes = [placedNode("a", -300, -100), placedNode("b", 500, 250)];

    const t = fitTransform(nodes, SIZE);

    for (const n of nodes) {
      const [sx, sy] = toScreen(t, n.x ?? 0, n.y ?? 0);
      expect(sx - n.r * t.k).toBeGreaterThanOrEqual(0);
      expect(sx + n.r * t.k).toBeLessThanOrEqual(SIZE.width);
      expect(sy - n.r * t.k).toBeGreaterThanOrEqual(0);
      expect(sy + n.r * t.k).toBeLessThanOrEqual(SIZE.height);
    }
    const [cx, cy] = toScreen(t, 100, 75);
    expect(Math.abs(cx - SIZE.width / 2)).toBeLessThan(1);
    expect(Math.abs(cy - SIZE.height / 2)).toBeLessThan(1);
  });

  it("does not blow a tiny graph up beyond the fit maximum", () => {
    expect(fitTransform([placedNode("a", 0, 0), placedNode("b", 5, 5)], SIZE).k).toBe(FIT_MAX_SCALE);
  });

  it("never zooms out past the minimum scale", () => {
    expect(fitTransform([placedNode("a", -1e6, 0), placedNode("b", 1e6, 0)], SIZE).k).toBe(ZOOM_MIN);
  });
});

describe("clampScale", () => {
  it("keeps the scale inside the extent", () => {
    expect(clampScale(0.001)).toBe(ZOOM_MIN);
    expect(clampScale(1000)).toBe(ZOOM_MAX);
    expect(clampScale(2)).toBe(2);
  });
});

describe("toGraph and toScreen", () => {
  it("are inverse to each other", () => {
    const t = { x: 40, y: -12, k: 2.5 };

    const [sx, sy] = toScreen(t, 17, -3);

    expect(toGraph(t, sx, sy)).toEqual([17, -3]);
  });
});

describe("nodeAtPoint", () => {
  const nodes = [placedNode("a", 0, 0), placedNode("b", 100, 0)];
  const t = { x: 400, y: 300, k: 1 };

  it("finds the node under the pointer", () => {
    expect(nodeAtPoint(nodes, t, 402, 301)?.id).toBe("a");
    expect(nodeAtPoint(nodes, t, 499, 300)?.id).toBe("b");
  });

  it("allows a few pixels of slack and no more", () => {
    expect(nodeAtPoint(nodes, t, 400 + 8 + 3, 300)?.id).toBe("a");
    expect(nodeAtPoint(nodes, t, 400 + 8 + 30, 300)).toBeUndefined();
  });

  it("picks the nearer of two overlapping nodes", () => {
    const close = [placedNode("far", 0, 0, 20), placedNode("near", 10, 0, 20)];

    expect(nodeAtPoint(close, t, 410, 300)?.id).toBe("near");
  });

  it("returns undefined for a node that has no position yet", () => {
    expect(nodeAtPoint([{ ...placedNode("a", 0, 0), x: undefined }], t, 400, 300)).toBeUndefined();
  });
});

describe("nearestInDirection", () => {
  const from = placedNode("from", 0, 0);
  const nodes = [from, placedNode("right", 50, 5), placedNode("farRight", 200, 0), placedNode("down", 0, 60), placedNode("up", 0, -80), placedNode("left", -70, 0)];

  it("moves to the nearest node on that side", () => {
    expect(nearestInDirection(nodes, from, "right")?.id).toBe("right");
    expect(nearestInDirection(nodes, from, "down")?.id).toBe("down");
    expect(nearestInDirection(nodes, from, "up")?.id).toBe("up");
    expect(nearestInDirection(nodes, from, "left")?.id).toBe("left");
  });

  it("prefers the node on the same row over a closer one far off to the side", () => {
    const row = [from, placedNode("diagonal", 30, 120), placedNode("sameRow", 90, 0)];

    expect(nearestInDirection(row, from, "right")?.id).toBe("sameRow");
  });

  it("stays put when nothing lies in that direction", () => {
    expect(nearestInDirection([from, placedNode("left", -10, 0)], from, "right")).toBeUndefined();
  });
});

describe("labelledNodes", () => {
  const nodes: GraphNode[] = Array.from({ length: 14 }, (_, i) => ({ id: `n${i}`, kind: "task", label: `n${i}`, weight: 1 }));
  const edges: GraphEdge[] = nodes.slice(1).map((n) => ({ source: "n0", target: n.id, kind: "x", weight: 1 }));
  const scene = buildScene(nodes, edges);
  scene.nodes.forEach((n, i) => {
    n.x = (i - 7) * 10;
    n.y = 0;
  });
  const centred = (k: number) => ({ x: SIZE.width / 2, y: SIZE.height / 2, k });

  it("labels the hubs and the highlighted nodes when zoomed out", () => {
    const labelled = labelledNodes(scene, centred(1), SIZE, new Set(["n13"])).map((n) => n.id);

    expect(labelled).toContain("n0");
    expect(labelled).toContain("n13");
    expect(labelled.length).toBeLessThan(scene.nodes.length);
  });

  it("labels every node in view once zoomed past the threshold", () => {
    expect(labelledNodes(scene, centred(LABEL_ZOOM_THRESHOLD), SIZE, null)).toHaveLength(scene.nodes.length);
  });

  it("never labels a node that is off the canvas", () => {
    const panned = { x: -5000, y: SIZE.height / 2, k: LABEL_ZOOM_THRESHOLD };

    expect(labelledNodes(scene, panned, SIZE, new Set(scene.nodes.map((n) => n.id)))).toHaveLength(0);
  });
});

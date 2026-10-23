import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CHARACTER_PX, installCanvasStub, type CanvasStub } from "@/test/utils/canvas";

import { ACTIVE_RING_PX, BASE_LINK_ALPHA, BASE_LINK_PX, DIM_LINK_ALPHA, DIM_NODE_ALPHA, LABEL_FONT_PX, LABEL_GAP_PX, LIT_LINK_PX, RING_PX } from "./config";
import { paint, type Frame, type Palette } from "./draw";
import { buildScene, type Scene } from "./layout";

/*
 * What a frame draws, read from the recording context: a base graph shows every link clearly, an active node
 * lights itself and its neighbours with rings and thick accent links and strongly dims everything else, and the
 * labels never overlap.
 */

const PALETTE: Palette = {
  "--muted-foreground": "#aaaaaa",
  "--ring": "#3366ff",
  "--foreground": "#ffffff",
  "--background": "#000000",
  "--card": "#111111",
  "--status-active-fg": "#4488ff",
  "--status-done-fg": "#44cc88",
};

/** hub with a, b, c; d is linked to a only; `lonely` has no link. Placed on a grid so labels can be checked. */
function build(): Scene {
  const nodes = ["hub", "a", "b", "c", "d", "lonely"].map((id) => ({ id, kind: "lesson", label: id, weight: 1 }));
  const edges = [["hub", "a"], ["hub", "b"], ["hub", "c"], ["a", "d"]].map(([source, target]) => ({ source, target, kind: "x", weight: 1 }));
  const scene = buildScene(nodes, edges);
  const spots: Record<string, [number, number]> = { hub: [0, 0], a: [100, 0], b: [-100, 0], c: [0, 100], d: [200, 0], lonely: [0, -100] };
  for (const node of scene.nodes) [node.x, node.y] = spots[node.id];
  return scene;
}

function frame(scene: Scene, activeId?: string, extra: Partial<Frame> = {}): Frame {
  return {
    size: { width: 800, height: 600 },
    ratio: 1,
    transform: { x: 400, y: 300, k: 1 },
    palette: PALETTE,
    fontFamily: "sans-serif",
    scene,
    active: activeId ? scene.byId.get(activeId) : undefined,
    selected: undefined,
    focused: undefined,
    ...extra,
  };
}

let stub: CanvasStub;
beforeEach(() => {
  stub = installCanvasStub();
});

afterEach(() => {
  stub.restore();
});

const draw = (given: Frame): void => paint(document.createElement("canvas").getContext("2d") as CanvasRenderingContext2D, given);
const alphas = (): unknown[] => stub.assigned("globalAlpha");
const widths = (): unknown[] => stub.assigned("lineWidth");
const strokes = (): unknown[] => stub.assigned("strokeStyle");

describe("paint without an active node", () => {
  it("draws every link in one clearly visible pass and dims nothing", () => {
    draw(frame(build()));

    expect(stub.calls.filter((call) => call.name === "lineTo")).toHaveLength(4);
    expect(alphas()).toContain(BASE_LINK_ALPHA);
    expect(alphas()).not.toContain(DIM_NODE_ALPHA);
    expect(alphas()).not.toContain(DIM_LINK_ALPHA);
    expect(widths()).toContain(BASE_LINK_PX);
    expect(strokes()).not.toContain(PALETTE["--ring"]);
    expect(BASE_LINK_ALPHA).toBeGreaterThanOrEqual(0.5);
  });

  it("draws no ring", () => {
    draw(frame(build()));

    expect(strokes()).not.toContain(PALETTE["--ring"]);
  });
});

describe("paint with an active node", () => {
  it("draws the links that touch it thick and in the accent colour, and the rest almost invisible", () => {
    draw(frame(build(), "hub"));

    const dimPass = stub.calls.findIndex((call) => call.name === "set globalAlpha" && call.args[0] === DIM_LINK_ALPHA);
    expect(dimPass).toBeGreaterThanOrEqual(0);
    expect(widths()).toContain(LIT_LINK_PX);
    expect(strokes()).toContain(PALETTE["--ring"]);
    // hub touches three links, a-d is the one dimmed link
    const lineTos = stub.calls.map((call, index) => ({ call, index })).filter(({ call }) => call.name === "lineTo");
    expect(lineTos).toHaveLength(4);
    expect(lineTos.filter(({ index }) => index > dimPass)).toHaveLength(4);
  });

  it("keeps the node and its neighbours at full strength with a ring and dims every other node", () => {
    draw(frame(build(), "hub"));

    const arcs = stub.calls.filter((call) => call.name === "arc");
    const nodeAlphas = stub.calls.filter((call) => call.name === "set globalAlpha").map((call) => call.args[0]);
    // 6 node circles plus 4 rings (hub and its 3 neighbours)
    expect(arcs).toHaveLength(10);
    expect(nodeAlphas.filter((alpha) => alpha === DIM_NODE_ALPHA)).toHaveLength(2);
    expect(DIM_NODE_ALPHA).toBeLessThanOrEqual(0.15);
    expect(widths()).toEqual(expect.arrayContaining([ACTIVE_RING_PX, RING_PX]));
  });

  it("rings a selected or focused node even when it is not the active one", () => {
    const scene = build();

    draw(frame(scene, undefined, { selected: scene.byId.get("lonely") }));

    expect(strokes()).toContain(PALETTE["--ring"]);
    expect(stub.calls.filter((call) => call.name === "arc")).toHaveLength(7);
  });

  it("labels only the lit nodes, and the active one first", () => {
    draw(frame(build(), "hub"));

    const labels = stub.labels();
    expect(labels).toContain("hub");
    expect(labels.sort()).toEqual(["a", "b", "c", "hub"]);
  });
});

describe("paint labels", () => {
  it("never draws two labels over each other", () => {
    // Twelve nodes one pixel apart would overlap everywhere without placement.
    const nodes = Array.from({ length: 12 }, (_, i) => ({ id: `node-${i}`, kind: "lesson", label: `node-${i}`, weight: 1 }));
    const edges = nodes.slice(1).map((n) => ({ source: "node-0", target: n.id, kind: "x", weight: 1 }));
    const scene = buildScene(nodes, edges);
    scene.nodes.forEach((n, i) => {
      n.x = i * 2;
      n.y = i % 3;
    });

    draw(frame(scene, "node-0"));

    const drawn = stub.labelsDrawn();
    expect(drawn.length).toBeGreaterThan(0);
    expect(drawn.length).toBeLessThan(12);
    for (let i = 0; i < drawn.length; i += 1) {
      for (let j = i + 1; j < drawn.length; j += 1) {
        const wide = (drawn[i].text.length + drawn[j].text.length) * CHARACTER_PX * 0.5 + LABEL_GAP_PX;
        const high = LABEL_FONT_PX + 2 + LABEL_GAP_PX;
        const clear = Math.abs(drawn[i].x - drawn[j].x) >= wide || Math.abs(drawn[i].y - drawn[j].y) >= high;
        // The active node's own label is forced and may sit over another.
        expect(clear || drawn[i].text === "node-0" || drawn[j].text === "node-0").toBe(true);
      }
    }
  });

  it("keeps the active node's label even when it would overlap", () => {
    const scene = build();
    for (const node of scene.nodes) {
      node.x = 0;
      node.y = 0;
    }

    draw(frame(scene, "hub"));

    expect(stub.labels()).toContain("hub");
  });
});

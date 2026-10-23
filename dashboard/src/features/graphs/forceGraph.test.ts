import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { installCanvasStub, type CanvasStub } from "@/test/utils/canvas";

import { DIM_NODE_ALPHA, FALLBACK_HEIGHT, FALLBACK_WIDTH, ZOOM_MAX } from "./config";
import { createGraphEngine, type Engine2D } from "./forceGraph";
import { buildScene, type SimNode } from "./layout";
import { centredTransform, fitTransform, nodeAtPoint, toGraph, toScreen } from "./view";

const GRAPH = memoryGraph.body.data;
const SIZE = { width: FALLBACK_WIDTH, height: FALLBACK_HEIGHT };

/** A manual animation-frame clock: nothing runs until the test pumps a frame. */
let queue: Map<number, FrameRequestCallback>;
let clock = 0;
let nextId = 0;
let stub: CanvasStub;
const engines: Engine2D[] = [];
const canvases: HTMLCanvasElement[] = [];

function pump(frames = 1): void {
  for (let frame = 0; frame < frames; frame += 1) {
    clock += 16;
    const due = [...queue.values()];
    queue.clear();
    for (const callback of due) callback(clock);
  }
}

function pumpUntilIdle(engine: Engine2D, limit = 5000): number {
  let frames = 0;
  while (engine.isAnimating() && frames < limit) {
    pump();
    frames += 1;
  }
  return frames;
}

/** A hub linked to `spokes` leaves: the leaves beyond the top ten are not hubs, so they get a label only when lit. */
const STAR = {
  nodes: [{ id: "hub", kind: "lesson", label: "hub", weight: 30 }, ...Array.from({ length: 30 }, (_, i) => ({ id: `leaf${i}`, kind: "task", label: `leaf${i}`, weight: 1 }))],
  edges: Array.from({ length: 30 }, (_, i) => ({ source: "hub", target: `leaf${i}`, kind: "task", weight: 1 })),
};

function setup(reducedMotion: boolean, graph: { nodes: typeof GRAPH.nodes; edges: typeof GRAPH.edges } = GRAPH) {
  const canvas = document.createElement("canvas");
  document.body.append(canvas);
  canvases.push(canvas);
  const scene = buildScene(graph.nodes, graph.edges);
  const onSelect = vi.fn();
  const engine = createGraphEngine(canvas, scene, { reducedMotion, onSelect });
  engines.push(engine);
  const node = (label: string): SimNode => scene.nodes.find((n) => n.label === label) as SimNode;
  const screenOf = (target: SimNode): [number, number] => toScreen(engine.transform(), target.x ?? 0, target.y ?? 0);
  return { canvas, scene, engine, onSelect, node, screenOf };
}

/** d3-drag and d3-zoom listen on `event.view`; jsdom refuses the vitest `window` stand-in as a constructor argument. */
function mouse(type: string, x: number, y: number): MouseEvent {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperty(event, "view", { value: window });
  return event;
}

beforeEach(() => {
  queue = new Map();
  clock = 0;
  nextId = 0;
  stub = installCanvasStub();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    nextId += 1;
    queue.set(nextId, callback);
    return nextId;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    queue.delete(id);
  });
});

afterEach(async () => {
  for (const engine of engines.splice(0)) engine.destroy();
  // d3-drag swallows the click that ends a drag until a zero-delay timer fires; let it, so it cannot reach the next test.
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (const canvas of canvases.splice(0)) canvas.remove();
  stub.restore();
  vi.restoreAllMocks();
});

describe("createGraphEngine, reduced motion", () => {
  it("settles the layout before the first frame, draws once and then holds still", () => {
    const { scene, engine } = setup(true);

    expect(scene.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))).toBe(true);
    expect(engine.isAnimating()).toBe(false);
    pump(3);

    expect(stub.calls.filter((call) => call.name === "clearRect")).toHaveLength(1);
    expect(queue.size).toBe(0);
  });

  it("fits the settled graph in the canvas at once", () => {
    const { scene, engine } = setup(true);

    const fit = fitTransform(scene.nodes, SIZE);

    expect(engine.transform().k).toBeCloseTo(fit.k, 5);
    expect(engine.transform().x).toBeCloseTo(fit.x, 3);
  });

  it("moves a dragged node with the pointer, without reheating, and drops it where it was released", () => {
    const { canvas, scene, engine, node, screenOf } = setup(true);
    const target = node("alpha-lesson");
    const [x, y] = screenOf(target);
    const before = { x: target.x ?? 0, y: target.y ?? 0 };
    const k = engine.transform().k;

    canvas.dispatchEvent(mouse("mousedown", x, y));
    window.dispatchEvent(mouse("mousemove", x + 40, y + 20));

    expect(target.fx).not.toBeNull();
    expect(target.x).toBeCloseTo(before.x + 40 / k, 3);
    expect(target.y).toBeCloseTo(before.y + 20 / k, 3);
    expect(engine.isAnimating()).toBe(false);
    window.dispatchEvent(mouse("mouseup", x + 40, y + 20));

    expect(target.fx).toBeNull();
    expect(target.x).toBeCloseTo(before.x + 40 / k, 3);
    expect(scene.nodes.filter((n) => n !== target).every((n) => n.fx == null)).toBe(true);
  });

  it("zooms about the canvas centre without animation", () => {
    const { engine } = setup(true);
    engine.reset();
    const centre = toGraph(engine.transform(), SIZE.width / 2, SIZE.height / 2);

    engine.zoomBy(1.25);

    expect(engine.transform().k).toBeCloseTo(1.25, 5);
    const after = toGraph(engine.transform(), SIZE.width / 2, SIZE.height / 2);
    expect(after[0]).toBeCloseTo(centre[0], 5);
    expect(after[1]).toBeCloseTo(centre[1], 5);
  });

  it("resets to the centred view and fits every node back into the canvas", () => {
    const { engine, scene } = setup(true);

    engine.reset();
    expect(engine.transform()).toMatchObject(centredTransform(SIZE));
    engine.fit();

    for (const n of scene.nodes) {
      const [sx, sy] = toScreen(engine.transform(), n.x ?? 0, n.y ?? 0);
      expect(sx).toBeGreaterThan(0);
      expect(sx).toBeLessThan(SIZE.width);
      expect(sy).toBeGreaterThan(0);
      expect(sy).toBeLessThan(SIZE.height);
    }
  });
});

describe("createGraphEngine, live simulation", () => {
  it("ticks frame by frame until the layout cools, fits the view, then stops asking for frames", () => {
    const { scene, engine } = setup(false);
    const first = scene.nodes[0].x;

    expect(engine.isAnimating()).toBe(true);
    pump();
    expect(scene.nodes[0].x).not.toBe(first);
    const frames = pumpUntilIdle(engine);

    expect(frames).toBeGreaterThan(20);
    expect(engine.isAnimating()).toBe(false);
    expect(queue.size).toBe(0);
    const fit = fitTransform(scene.nodes, SIZE);
    expect(engine.transform().k).toBeCloseTo(fit.k, 1);
    expect(stub.labels().length).toBeGreaterThan(0);
  });

  it("pins a dragged node and reheats the layout while it is held, then lets it go and cools again", () => {
    const { canvas, engine, node, screenOf } = setup(false);
    pumpUntilIdle(engine);
    const target = node("hooks");
    const [x, y] = screenOf(target);
    const k = engine.transform().k;
    const before = target.x ?? 0;

    canvas.dispatchEvent(mouse("mousedown", x, y));
    expect(engine.isAnimating()).toBe(true);
    window.dispatchEvent(mouse("mousemove", x + 60, y));
    pump(5);

    expect(target.fx).toBeCloseTo(before + 60 / k, 3);
    expect(target.x).toBeCloseTo(before + 60 / k, 3);
    window.dispatchEvent(mouse("mouseup", x + 60, y));
    expect(target.fx).toBeNull();
    pumpUntilIdle(engine);

    expect(engine.isAnimating()).toBe(false);
  });

  it("pans the view when the background is dragged", () => {
    const { canvas, engine, scene, onSelect } = setup(false);
    pumpUntilIdle(engine);
    expect(nodeAtPoint(scene.nodes, engine.transform(), 3, 3)).toBeUndefined();
    const before = engine.transform();

    canvas.dispatchEvent(mouse("mousedown", 3, 3));
    window.dispatchEvent(mouse("mousemove", 33, 23));
    window.dispatchEvent(mouse("mouseup", 33, 23));

    expect(engine.transform().x).toBeCloseTo(before.x + 30, 3);
    expect(engine.transform().y).toBeCloseTo(before.y + 20, 3);
    expect(engine.transform().k).toBeCloseTo(before.k, 5);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("zooms with the wheel and never past the scale limit", () => {
    const { canvas, engine } = setup(false);
    pumpUntilIdle(engine);
    const before = engine.transform().k;

    canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: -200, clientX: 400, clientY: 260, bubbles: true, cancelable: true }));
    expect(engine.transform().k).toBeGreaterThan(before);
    for (let i = 0; i < 30; i += 1) {
      canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: -500, clientX: 400, clientY: 260, bubbles: true, cancelable: true }));
    }

    expect(engine.transform().k).toBe(ZOOM_MAX);
  });

  it("does not take the automatic fit back after the user zoomed", () => {
    const { canvas, engine } = setup(false);
    canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: -200, clientX: 400, clientY: 260, bubbles: true, cancelable: true }));
    const zoomed = engine.transform().k;

    pumpUntilIdle(engine);

    expect(engine.transform().k).toBeCloseTo(zoomed, 5);
  });

  it("animates a zoom step to the same place a reduced-motion zoom lands", () => {
    const { engine } = setup(false);
    pumpUntilIdle(engine);
    const start = engine.transform().k;

    engine.zoomBy(1.25);
    pumpUntilIdle(engine);

    expect(engine.transform().k).toBeCloseTo(start * 1.25, 5);
  });

  it("reheats the layout on reset", () => {
    const { engine } = setup(false);
    pumpUntilIdle(engine);

    engine.reset();

    expect(engine.isAnimating()).toBe(true);
  });
});

describe("createGraphEngine, pointer and keyboard helpers", () => {
  it("reports a click on a node and on the background", () => {
    const { canvas, engine, onSelect, node, screenOf } = setup(true);
    const [x, y] = screenOf(node("alpha-lesson"));

    canvas.dispatchEvent(mouse("click", x, y));
    canvas.dispatchEvent(mouse("click", 3, 3));

    expect(onSelect).toHaveBeenNthCalledWith(1, "lesson:1");
    expect(onSelect).toHaveBeenNthCalledWith(2, null);
    expect(engine.isAnimating()).toBe(false);
  });

  it("lights a hovered node's neighbourhood, labels only it and strongly dims the rest, then goes back", () => {
    const { canvas, engine, node, screenOf } = setup(true, STAR);
    engine.reset();
    pump();
    stub.clear();
    const [x, y] = screenOf(node("leaf5"));

    canvas.dispatchEvent(mouse("pointermove", x, y));
    pump();

    expect(canvas.dataset.hover).toBe("true");
    expect(stub.labels().sort()).toEqual(["hub", "leaf5"]);
    expect(stub.assigned("globalAlpha")).toContain(DIM_NODE_ALPHA);
    stub.clear();
    canvas.dispatchEvent(mouse("pointerleave", x, y));
    pump();

    expect(canvas.dataset.hover).toBe("false");
    expect(stub.labels()).toContain("hub");
    expect(stub.labels()).not.toContain("leaf29");
    expect(stub.assigned("globalAlpha")).not.toContain(DIM_NODE_ALPHA);
  });

  it("labels a crowded neighbourhood without overlap: some labels give way to others", () => {
    const { canvas, engine, node, screenOf } = setup(true, STAR);
    engine.reset();
    pump();
    stub.clear();
    const [x, y] = screenOf(node("hub"));

    canvas.dispatchEvent(mouse("pointermove", x, y));
    pump();

    const drawn = stub.labelsDrawn();
    expect(drawn.map((label) => label.text)).toContain("hub");
    expect(drawn.length).toBeGreaterThan(1);
    expect(drawn.length).toBeLessThan(31);
  });

  it("labels the selected and the keyboard-focused node", () => {
    const { engine } = setup(true);
    pump();
    stub.clear();

    engine.setView({ selected: "lesson:1", focused: "pattern:2" });
    pump();

    expect(stub.labels()).toEqual(expect.arrayContaining(["alpha-lesson", "task-0106", "pattern-two"]));
  });

  it("moves to the nearest node in a direction and says when there is none", () => {
    const { engine, scene } = setup(true);
    const leftmost = [...scene.nodes].sort((a, b) => (a.x ?? 0) - (b.x ?? 0))[0];
    const rightmost = [...scene.nodes].sort((a, b) => (b.x ?? 0) - (a.x ?? 0))[0];

    const next = engine.move(leftmost.id, "right");

    expect(next).not.toBeNull();
    expect(scene.byId.get(next as string)?.x).toBeGreaterThan(leftmost.x ?? 0);
    expect(engine.move(rightmost.id, "right")).toBeNull();
    expect(engine.move("ghost", "right")).toBeNull();
  });

  it("brings a node that is off the canvas into view", () => {
    const { engine, scene } = setup(true);
    const target = scene.nodes[0];
    engine.reset();
    target.x = 5000;
    target.y = 5000;

    engine.reveal(target.id);

    const [sx, sy] = toScreen(engine.transform(), 5000, 5000);
    expect(sx).toBeCloseTo(SIZE.width / 2, 3);
    expect(sy).toBeCloseTo(SIZE.height / 2, 3);
  });

  it("repaints when the theme class changes", async () => {
    setup(true);
    pump();
    stub.clear();

    document.documentElement.classList.add("dark");
    await new Promise((resolve) => setTimeout(resolve, 0));
    pump();

    expect(stub.calls.some((call) => call.name === "clearRect")).toBe(true);
  });
});

describe("createGraphEngine, destroy", () => {
  it("cancels the pending frame and ignores later input", () => {
    const { canvas, engine, onSelect, node, screenOf } = setup(false);
    const [x, y] = screenOf(node("alpha-lesson"));
    expect(queue.size).toBe(1);

    engine.destroy();

    expect(queue.size).toBe(0);
    stub.clear();
    canvas.dispatchEvent(mouse("click", x, y));
    canvas.dispatchEvent(mouse("pointermove", x, y));
    pump(3);
    expect(onSelect).not.toHaveBeenCalled();
    expect(stub.calls).toHaveLength(0);
  });
});

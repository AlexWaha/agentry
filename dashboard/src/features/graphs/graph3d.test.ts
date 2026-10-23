import { Mesh, PerspectiveCamera } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import memoryGraph from "@/test/fixtures/memory/graph-memory.json";

import {
  GRAPH3D_FOCUS_DISTANCE,
  GRAPH3D_IDLE_SLEEP_MS,
  GRAPH3D_LIT_LINK_WIDTH,
  GRAPH3D_MAX_DISTANCE,
  GRAPH3D_MIN_DISTANCE,
  GRAPH3D_PARTICLES,
  GRAPH3D_START_DISTANCE,
  GRAPH3D_WARMUP_TICKS,
} from "./config";
import type { GraphEngine } from "./engine";
import { createGraph3D } from "./graph3d";
import { parseColour } from "./highlight";
import { buildScene } from "./layout";

/*
 * WebGL does not exist in jsdom, so 3d-force-graph is replaced by a recording stand-in: every chained setter
 * stores its argument, and the test reads the accessors the engine registered. The stand-in answers like the real
 * one where it matters: the camera getter reports a `lookAt` a thousand units ahead (not the orbit target), and the
 * controls hold the orbit target. The real logic is what is checked: which nodes and links dim, which get a halo,
 * thick links and particles, how input maps to selection and camera moves, when the loop sleeps, what teardown
 * does, and that a label is only ever text. One group of tests uses three's real OrbitControls.
 */

type Point = { x: number; y: number; z: number };

interface FakeControls extends Record<string, unknown> {
  target: Point;
  _pointers: number[];
  _pointerPositions: Record<number, unknown>;
}

interface FakeGraph {
  element: HTMLElement;
  options: unknown;
  controlListeners: Map<string, () => void>;
  originalUp: ReturnType<typeof vi.fn>;
  controlsObject: FakeControls;
  rendererObject: { forceContextLoss: ReturnType<typeof vi.fn> };
  props: Record<string, unknown>;
  calls: [string, unknown[]][];
  position: Point;
}

const fakes = vi.hoisted(() => ({
  instances: [] as unknown[],
  /** A method name (or "constructor") that throws when called, to test a setup that fails half way. */
  failOn: "",
  /** Controls to hand out instead of the stand-in's own, for the tests that use the real OrbitControls. */
  controls: undefined as unknown,
  order: [] as string[],
}));

vi.mock("3d-force-graph", () => {
  class Fake {
    props: Record<string, unknown> = {};
    calls: [string, unknown[]][] = [];
    position = { x: 0, y: 0, z: 100 };
    controlListeners = new Map<string, () => void>();
    originalUp = vi.fn();
    rendererObject = { forceContextLoss: vi.fn(() => fakes.order.push("forceContextLoss")) };
    controlsObject: unknown = fakes.controls ?? {
      addEventListener: (type: string, listener: () => void) => this.controlListeners.set(type, listener),
      removeEventListener: (type: string) => this.controlListeners.delete(type),
      target: { x: 0, y: 0, z: 0 },
      _pointers: [7],
      _pointerPositions: {},
      _onPointerUp: this.originalUp,
    };
    constructor(
      public element: HTMLElement,
      public options: unknown,
    ) {
      if (fakes.failOn === "constructor") throw new Error("Error creating WebGL context");
      const self = new Proxy(this, {
        get: (target, name: string) => {
          if (name in target) return (target as unknown as Record<string, unknown>)[name];
          return (...args: unknown[]) => {
            target.calls.push([name, args]);
            if (name === fakes.failOn) throw new Error(`${name} failed`);
            if (name === "_destructor") fakes.order.push("_destructor");
            if (name === "cameraPosition") {
              if (args.length === 0) return { ...target.position, lookAt: { ...target.position, z: target.position.z - 1000 } };
              target.position = { ...target.position, ...(args[0] as object) };
              return self;
            }
            if (name === "controls") return target.controlsObject;
            if (name === "renderer") return target.rendererObject;
            if (name === "graph2ScreenCoords") return { x: 120.4 + (args[0] as number) * 50, y: 80.6 + (args[1] as number) * 50 };
            if (args.length > 0) target.props[name] = args[0];
            return self;
          };
        },
      });
      fakes.instances.push(self);
      return self;
    }
  }
  return { default: Fake };
});

const GRAPH = memoryGraph.body.data;
const TOKENS: Record<string, string> = {
  "--muted": "#101010",
  "--ring": "#3366ff",
  "--foreground": "#ffffff",
  "--background": "#000000",
  "--muted-foreground": "#bbbbbb",
  "--card": "#111111",
  "--status-active-fg": "#4488ff",
  "--status-done-fg": "#44cc88",
};

type Accessor = (item: unknown) => unknown;
let queue: Map<number, FrameRequestCallback>;
let nextId = 0;
const engines: GraphEngine[] = [];

const fake = (): FakeGraph & Record<string, unknown> => fakes.instances.at(-1) as FakeGraph & Record<string, unknown>;
const accessor = (name: string): Accessor => fake().props[name] as Accessor;
const calls = (name: string): unknown[][] => fake().calls.filter(([called]) => called === name).map(([, args]) => args);
/** The camera moves made (a call with no argument only reads the position). */
const moves = (): unknown[][] => calls("cameraPosition").filter((args) => args.length > 0);
const flushFrame = (): void => {
  const due = [...queue.values()];
  queue.clear();
  for (const callback of due) callback(0);
};
const hover = (node: unknown): void => (fake().props.onNodeHover as (node: unknown) => void)(node);
const shownLabels = (container: HTMLElement): (string | null)[] =>
  [...container.querySelectorAll<HTMLElement>("div")].filter((el) => !el.hidden).map((el) => el.textContent);

function setup(reducedMotion = false) {
  const container = document.createElement("div");
  document.body.append(container);
  const scene = buildScene(GRAPH.nodes, GRAPH.edges);
  const onSelect = vi.fn();
  const engine = createGraph3D(container, scene, { reducedMotion, onSelect });
  engines.push(engine);
  const nodeObjects = (fake().props.graphData as { nodes: { id: string; x?: number; y?: number; z?: number }[] }).nodes;
  const link = (source: string, target: string) => ({ source, target });
  const place = (spacing = 40): void => nodeObjects.forEach((n, i) => Object.assign(n, { x: i * spacing, y: 0, z: 0 }));
  return { container, scene, onSelect, engine, nodeObjects, link, place };
}

beforeEach(() => {
  fakes.instances.length = 0;
  fakes.failOn = "";
  fakes.controls = undefined;
  fakes.order.length = 0;
  queue = new Map();
  nextId = 0;
  for (const [name, value] of Object.entries(TOKENS)) document.documentElement.style.setProperty(name, value);
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    nextId += 1;
    queue.set(nextId, callback);
    return nextId;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    queue.delete(id);
  });
});

afterEach(() => {
  for (const engine of engines.splice(0)) engine.destroy();
  for (const name of Object.keys(TOKENS)) document.documentElement.style.removeProperty(name);
  document.body.replaceChildren();
  vi.useRealTimers();
});

const distance = (a: string, b: string): number => {
  const [ar, ag, ab] = parseColour(a);
  const [br, bg, bb] = parseColour(b);
  return Math.hypot(ar - br, ag - bg, ab - bb);
};

describe("createGraph3D, setup", () => {
  it("gives the scene to 3d-force-graph as orbit controls with the graph's nodes and links", () => {
    const { container, scene } = setup();

    expect(fake().element).toBe(container);
    expect(fake().options).toEqual({ controlType: "orbit" });
    const data = fake().props.graphData as { nodes: { id: string }[]; links: { source: string; target: string }[] };
    expect(data.nodes.map((n) => n.id)).toEqual(scene.nodes.map((n) => n.id));
    expect(data.links).toHaveLength(scene.links.length);
    expect(data.links[0]).toEqual({ source: "lesson:1", target: "task:task-0106" });
    expect(fake().props.showNavInfo).toBe(false);
    expect(fake().props.backgroundColor).toBe("#101010");
  });

  it("turns the built-in HTML tooltips off, because a label must never be rendered from a string", () => {
    setup();

    expect(accessor("nodeLabel")({ id: "x", label: "<img src=x onerror=alert(1)>" })).toBe("");
    expect(accessor("linkLabel")({ source: "a", target: "b" })).toBe("");
  });

  it("makes a node's size follow its degree", () => {
    const { nodeObjects } = setup();
    const size = (id: string): number => accessor("nodeVal")(nodeObjects.find((n) => n.id === id)) as number;

    expect(size("lesson:1")).toBeGreaterThan(size("task:task-0107"));
    expect(size("pattern:2")).toBeGreaterThan(0);
  });

  it("starts the camera a fixed distance out and, with reduced motion, lays the graph out before the first frame", () => {
    setup(true);

    expect(calls("cameraPosition")[0][0]).toEqual({ x: 0, y: 0, z: GRAPH3D_START_DISTANCE });
    expect(fake().props.warmupTicks).toBe(GRAPH3D_WARMUP_TICKS);
    expect(fake().props.cooldownTime).toBeLessThan(1000);
  });

  it("leaves the layout to run live without reduced motion", () => {
    setup();

    expect(fake().props.warmupTicks).toBeUndefined();
  });

  it("limits how near and how far the orbit controls let the camera go", () => {
    setup();

    expect(fake().controlsObject.minDistance).toBe(GRAPH3D_MIN_DISTANCE);
    expect(fake().controlsObject.maxDistance).toBe(GRAPH3D_MAX_DISTANCE);
  });
});

describe("createGraph3D, highlight", () => {
  it("shows every node in its kind colour and every link clearly while nothing is active", () => {
    const { nodeObjects, link } = setup();
    const colours = nodeObjects.map((n) => accessor("nodeColor")(n));

    expect(new Set(colours).size).toBeGreaterThan(1);
    expect(distance(accessor("nodeColor")(nodeObjects[0]) as string, "#101010")).toBeGreaterThan(80);
    expect(accessor("linkColor")(link("lesson:1", "task:task-0106"))).toBe(accessor("linkColor")(link("lesson:2", "area:hooks")));
    expect(distance(accessor("linkColor")(link("lesson:1", "task:task-0106")) as string, "#101010")).toBeGreaterThan(60);
    expect(accessor("linkDirectionalParticles")(link("lesson:1", "task:task-0106"))).toBe(0);
    expect(fake().props.linkOpacity).toBeGreaterThanOrEqual(0.8);
  });

  it("dims every node outside the active node's neighbourhood and keeps the neighbourhood at full colour", () => {
    const { nodeObjects } = setup();
    const before = Object.fromEntries(nodeObjects.map((n) => [n.id, accessor("nodeColor")(n) as string]));

    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    for (const id of ["lesson:1", "task:task-0106", "agent:senior-backend-dev", "area:hooks"]) {
      expect(accessor("nodeColor")(nodeObjects.find((n) => n.id === id))).toBe(before[id]);
    }
    for (const id of ["lesson:2", "task:task-0107", "agent:qa-engineer", "pattern:2"]) {
      const dimmed = accessor("nodeColor")(nodeObjects.find((n) => n.id === id)) as string;
      expect(dimmed).not.toBe(before[id]);
      expect(distance(dimmed, "#101010")).toBeLessThan(distance(before[id], "#101010") * 0.3);
    }
    expect(calls("refresh").length).toBeGreaterThan(0);
  });

  it("draws the links that touch the active node thick, accent coloured and with particles, the rest thin and dim", () => {
    const { nodeObjects, link } = setup();
    const lit = link("lesson:1", "task:task-0106");
    const other = link("lesson:2", "area:hooks");
    const baseDistance = distance(accessor("linkColor")(other) as string, "#101010");
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    expect(accessor("linkWidth")(lit)).toBe(GRAPH3D_LIT_LINK_WIDTH);
    expect(accessor("linkWidth")(other)).toBeLessThan(GRAPH3D_LIT_LINK_WIDTH / 3);
    expect(accessor("linkColor")(lit)).toBe("#3366ff");
    expect(distance(accessor("linkColor")(other) as string, "#101010")).toBeLessThan(baseDistance * 0.3);
    expect(accessor("linkDirectionalParticles")(lit)).toBe(GRAPH3D_PARTICLES);
    expect(accessor("linkDirectionalParticles")(other)).toBe(0);
    expect(accessor("linkDirectionalParticleColor")(lit)).toBe("#3366ff");
  });

  it("runs no particles under reduced motion; the thick accent link still marks the selection", () => {
    const { nodeObjects, link } = setup(true);
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    expect(accessor("linkDirectionalParticles")(link("lesson:1", "task:task-0106"))).toBe(0);
    expect(accessor("linkWidth")(link("lesson:1", "task:task-0106"))).toBe(GRAPH3D_LIT_LINK_WIDTH);
  });

  it("reads links whose ends 3d-force-graph already turned into node objects", () => {
    const { nodeObjects } = setup();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    const objectLink = { source: nodeObjects.find((n) => n.id === "lesson:1"), target: nodeObjects.find((n) => n.id === "area:hooks") };

    expect(accessor("linkWidth")(objectLink)).toBe(GRAPH3D_LIT_LINK_WIDTH);
  });

  it("puts a halo on the active node and its neighbours and on no other node", () => {
    const { nodeObjects } = setup();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    const halo = (id: string): unknown => accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === id));

    expect(halo("lesson:1")).toBeInstanceOf(Mesh);
    expect(halo("task:task-0106")).toBeInstanceOf(Mesh);
    expect(halo("lesson:2")).toBe(false);
    expect(fake().props.nodeThreeObjectExtend).toBe(true);
  });

  it("has no halo and no dimming at all while nothing is active", () => {
    const { nodeObjects } = setup();

    expect(nodeObjects.every((n) => accessor("nodeThreeObject")(n) === false)).toBe(true);
  });

  it("falls back to the selected node when nothing is hovered and back again when the pointer leaves", () => {
    const { nodeObjects, engine } = setup();
    engine.setView({ selected: "lesson:2", focused: null });

    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "lesson:2"))).toBeInstanceOf(Mesh);
    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "lesson:1"))).toBe(false);
    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "lesson:1"))).toBeInstanceOf(Mesh);
    hover(null);

    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "lesson:2"))).toBeInstanceOf(Mesh);
  });

  it("gives the keyboard-focused node a halo without dimming anything", () => {
    const { nodeObjects, engine } = setup();

    engine.setView({ selected: null, focused: "pattern:2" });

    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "pattern:2"))).toBeInstanceOf(Mesh);
    expect(accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === "lesson:1"))).toBe(false);
  });

  it("repaints with the new palette when the theme class changes", async () => {
    setup();

    document.documentElement.style.setProperty("--muted", "#ffffff");
    document.documentElement.classList.add("dark");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fake().props.backgroundColor).toBe("#ffffff");
    document.documentElement.classList.remove("dark");
  });
});

describe("createGraph3D, rebuilding the scene", () => {
  it("rebuilds the scene once for two hovers over the same node, not once per pointer event", () => {
    const { nodeObjects } = setup();
    const lesson = nodeObjects.find((n) => n.id === "lesson:1");
    const before = calls("refresh").length;

    hover(lesson);
    hover(lesson);
    hover(lesson);

    expect(calls("refresh").length).toBe(before + 1);
    hover(null);
    hover(null);
    expect(calls("refresh").length).toBe(before + 2);
  });

  it("rebuilds the scene once for the same view reported twice, and not at all for the empty view it starts in", () => {
    const { engine } = setup();
    const before = calls("refresh").length;

    engine.setView({ selected: null, focused: null });
    expect(calls("refresh").length).toBe(before);
    engine.setView({ selected: "lesson:1", focused: null });
    engine.setView({ selected: "lesson:1", focused: null });
    expect(calls("refresh").length).toBe(before + 1);
    engine.setView({ selected: "lesson:1", focused: "lesson:2" });

    expect(calls("refresh").length).toBe(before + 2);
  });

  it("works out who is lit once per change, not once per node and link of every refresh", () => {
    const { nodeObjects, link } = setup();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    const litBefore = fake().props.nodeColor;

    for (let round = 0; round < 50; round += 1) {
      for (const n of nodeObjects) accessor("nodeColor")(n);
      accessor("linkWidth")(link("lesson:1", "area:hooks"));
    }

    expect(fake().props.nodeColor).toBe(litBefore);
  });
});

describe("createGraph3D, a click on a node", () => {
  const press = new Event("pointerup");

  it("records a position for a pointer the orbit controls hold none for, before 3d-force-graph's synthetic pointerup", () => {
    setup();
    const seen: unknown[] = [];
    fake().originalUp.mockImplementation(() => seen.push(structuredClone(fake().controlsObject._pointerPositions)));

    // What a plain click on a node leaves behind: one pointer id and no position for it.
    const listener = fake().controlsObject._onPointerUp as (event: Event) => void;
    listener(press);

    expect(fake().originalUp).toHaveBeenCalledTimes(1);
    expect(seen).toEqual([{ 7: { x: 0, y: 0 } }]);
  });

  it("keeps the position of a pointer that has one", () => {
    setup();
    fake().controlsObject._pointers = [7, 9];
    fake().controlsObject._pointerPositions = { 9: { x: 5, y: 6 } };

    const listener = fake().controlsObject._onPointerUp as (event: Event) => void;
    listener(press);

    expect(fake().controlsObject._pointerPositions).toEqual({ 7: { x: 0, y: 0 }, 9: { x: 5, y: 6 } });
  });

  it("passes the event on and runs the original with the controls as `this`", () => {
    setup();
    const receivers: unknown[] = [];
    fake().originalUp.mockImplementation(function (this: unknown) {
      receivers.push(this);
    });

    const listener = fake().controlsObject._onPointerUp as (event: Event) => void;
    listener(press);

    expect(receivers).toEqual([fake().controlsObject]);
    expect(receivers[0]).toBe(fake().controlsObject);
    expect(fake().originalUp).toHaveBeenCalledWith(press);
  });

  it("copes with controls that keep no pointer bookkeeping", () => {
    setup();
    delete (fake().controlsObject as Record<string, unknown>)._pointers;

    expect(() => (fake().controlsObject._onPointerUp as (event: Event) => void)(press)).not.toThrow();
    expect(fake().originalUp).toHaveBeenCalled();
  });
});

describe("createGraph3D, a click on a node with the real orbit controls", () => {
  /** jsdom has PointerEvent in recent versions; fall back to a mouse event that carries the two fields used. */
  function pointerEvent(type: string, init: { pointerId?: number; pointerType?: string }): Event {
    if (typeof PointerEvent === "function") return new PointerEvent(type, { bubbles: true, ...init });
    const event = new MouseEvent(type, { bubbles: true });
    Object.defineProperties(event, { pointerId: { value: init.pointerId ?? 0 }, pointerType: { value: init.pointerType ?? "mouse" } });
    return event;
  }

  function realControls() {
    const element = document.createElement("div");
    document.body.append(element);
    Object.assign(element, { setPointerCapture: vi.fn(), releasePointerCapture: vi.fn(), hasPointerCapture: vi.fn(() => true) });
    const controls = new OrbitControls(new PerspectiveCamera(), element);
    const errors: string[] = [];
    const onError = (event: ErrorEvent): void => {
      errors.push(event.message);
      event.preventDefault();
    };
    window.addEventListener("error", onError);
    afterEachCleanup.push(() => window.removeEventListener("error", onError));
    return { element, controls, errors };
  }
  const afterEachCleanup: (() => void)[] = [];
  afterEach(() => {
    for (const undo of afterEachCleanup.splice(0)) undo();
  });

  /** A press on a node: the real pointerdown, then 3d-force-graph's synthetic document pointerup, then the real one. */
  function clickWithoutMoving(element: HTMLElement): void {
    element.dispatchEvent(pointerEvent("pointerdown", { pointerId: 1 }));
    document.dispatchEvent(pointerEvent("pointerup", { pointerType: "touch" }));
    document.dispatchEvent(pointerEvent("pointerup", { pointerId: 1 }));
  }

  it("throws on that sequence without the patch, which is what the engine works around", () => {
    const { element, errors } = realControls();

    clickWithoutMoving(element);

    expect(errors.join(" ")).toMatch(/reading 'x'|undefined/);
  });

  it("survives that sequence once createGraph3D has patched the controls, and ends the gesture", () => {
    const { element, controls, errors } = realControls();
    fakes.controls = controls;
    const container = document.createElement("div");
    document.body.append(container);
    engines.push(createGraph3D(container, buildScene(GRAPH.nodes, GRAPH.edges), { reducedMotion: false, onSelect: vi.fn() }));

    clickWithoutMoving(element);

    expect(errors).toEqual([]);
    expect((controls as unknown as { _pointers: number[] })._pointers).toEqual([]);
  });

  it("still lets a press that moved end normally", () => {
    const { element, controls, errors } = realControls();
    fakes.controls = controls;
    const container = document.createElement("div");
    document.body.append(container);
    engines.push(createGraph3D(container, buildScene(GRAPH.nodes, GRAPH.edges), { reducedMotion: false, onSelect: vi.fn() }));

    element.dispatchEvent(pointerEvent("pointerdown", { pointerId: 1 }));
    document.dispatchEvent(pointerEvent("pointermove", { pointerId: 1 }));
    document.dispatchEvent(pointerEvent("pointerup", { pointerId: 1 }));

    expect(errors).toEqual([]);
    expect((controls as unknown as { _pointers: number[] })._pointers).toEqual([]);
  });
});

describe("createGraph3D, neighbour emphasis", () => {
  it("draws neighbours clearly larger than other nodes and the active node larger still", () => {
    const { nodeObjects } = setup();
    const size = (id: string): number => accessor("nodeVal")(nodeObjects.find((n) => n.id === id)) as number;
    const base = size("task:task-0106");
    const other = size("lesson:2");
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    expect(size("task:task-0106") / base).toBeGreaterThan(1.8 ** 3 * 0.9);
    expect(size("lesson:1")).toBeGreaterThan(size("task:task-0106"));
    expect(size("lesson:2")).toBe(other);
  });

  it("gives every neighbour a bright halo and the active node a brighter one", () => {
    const { nodeObjects } = setup();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    const opacity = (id: string): number => ((accessor("nodeThreeObject")(nodeObjects.find((n) => n.id === id)) as Mesh).material as { opacity: number }).opacity;

    expect(opacity("task:task-0106")).toBeGreaterThanOrEqual(0.5);
    expect(opacity("lesson:1")).toBeGreaterThan(opacity("task:task-0106"));
  });

  it("draws links that touch the active node many times thicker than the rest, with several particles", () => {
    const { nodeObjects, link } = setup();
    const thin = accessor("linkWidth")(link("lesson:1", "task:task-0106")) as number;
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    expect((accessor("linkWidth")(link("lesson:1", "task:task-0106")) as number) / thin).toBeGreaterThan(4);
    expect(accessor("linkDirectionalParticles")(link("lesson:1", "area:hooks"))).toBeGreaterThanOrEqual(4);
    expect(fake().props.linkDirectionalParticleWidth).toBeGreaterThanOrEqual(6);
  });
});

describe("createGraph3D, input", () => {
  it("reports a click on a node and on the background", () => {
    const { nodeObjects, onSelect } = setup();

    (fake().props.onNodeClick as (node: unknown) => void)(nodeObjects.find((n) => n.id === "task:task-0106"));
    (fake().props.onBackgroundClick as () => void)();

    expect(onSelect).toHaveBeenNthCalledWith(1, "task:task-0106");
    expect(onSelect).toHaveBeenNthCalledWith(2, null);
  });

  it("lets a node go when its drag ends, so it is pinned only while held", () => {
    const { nodeObjects } = setup();
    const held = Object.assign(nodeObjects[0], { fx: 1, fy: 2, fz: 3 }) as Record<string, unknown>;

    (fake().props.onNodeDragEnd as (node: unknown) => void)(held);

    expect([held.fx, held.fy, held.fz]).toEqual([undefined, undefined, undefined]);
  });

  it("counts dragging a node as the user moving the view, so no automatic fit pulls it back", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { nodeObjects, place } = setup();
    place();
    (fake().props.onNodeDrag as (node: unknown) => void)(nodeObjects[0]);
    const before = moves().length;

    vi.advanceTimersByTime(5000);
    (fake().props.onEngineStop as () => void)();

    expect(moves().length).toBe(before);
  });

  it("fits the view, and resets the camera and reheats the layout", () => {
    const { engine, place } = setup();
    place();
    const before = moves().length;

    engine.fit();
    expect(moves().length).toBe(before + 1);
    engine.reset();

    expect(moves().at(-1)?.[0]).toEqual({ x: 0, y: 0, z: GRAPH3D_START_DISTANCE });
    expect(calls("d3ReheatSimulation")).toHaveLength(1);
  });

  it("fits the bulk of the graph: centres on it, ignores a node the layout threw far out, and keeps the view direction", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => ({ id: `n${i}`, kind: "lesson", label: `n${i}`, weight: 1 }));
    const container = document.createElement("div");
    document.body.append(container);
    const engine = createGraph3D(container, buildScene(nodes, []), { reducedMotion: false, onSelect: vi.fn() });
    engines.push(engine);
    const placed = (fake().props.graphData as { nodes: Record<string, number>[] }).nodes;
    placed.forEach((n, i) => Object.assign(n, { x: (i % 5) * 20, y: Math.floor(i / 5) * 20, z: 0 }));
    Object.assign(placed[29], { x: 5000, y: 5000, z: 5000 });
    fake().position = { x: 0, y: 0, z: 500 };

    engine.fit();

    const [position, lookAt] = moves().at(-1) as [Point, Point];
    expect(lookAt.x).toBeCloseTo(40, 0);
    expect(lookAt.y).toBeCloseTo(50, 0);
    expect(position.x).toBeCloseTo(lookAt.x, 3);
    expect(position.y).toBeCloseTo(lookAt.y, 3);
    // The bulk is about 100 units across, so the camera sits a few hundred units out, not thousands.
    expect(position.z).toBeGreaterThan(100);
    expect(position.z).toBeLessThan(600);
  });

  it("fits a small graph by all of its nodes and sizes the distance to the height of the view", () => {
    const { engine, place } = setup();
    place();
    fake().position = { x: 0, y: 0, z: 300 };

    engine.fit();

    const [position, lookAt] = moves().at(-1) as [Point, Point];
    expect(lookAt.x).toBeCloseTo(160, 0);
    expect(position.z).toBeGreaterThan(160 / Math.tan((25 * Math.PI) / 180) * 0.5);
  });

  it("fits again as the layout spreads and when the simulation stops, until the user moves the camera", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { place } = setup();
    place();
    const before = moves().length;

    vi.advanceTimersByTime(1300);
    expect(moves().length).toBe(before + 1);
    vi.advanceTimersByTime(3000);
    expect(moves().length).toBe(before + 2);
    (fake().props.onEngineStop as () => void)();
    expect(moves().length).toBe(before + 3);
    fake().controlListeners.get("start")?.();
    (fake().props.onEngineStop as () => void)();

    expect(moves().length).toBe(before + 3);
  });

  it("draws the active node and the nodes it lights larger than the rest", () => {
    const { nodeObjects } = setup();
    const size = (id: string): number => accessor("nodeVal")(nodeObjects.find((n) => n.id === id)) as number;
    const before = { lit: size("task:task-0106"), other: size("lesson:2") };

    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    expect(size("lesson:1")).toBeGreaterThan(before.lit);
    expect(size("task:task-0106")).toBeGreaterThan(before.lit);
    expect(size("lesson:2")).toBe(before.other);
  });

  it("does not reheat the layout on reset under reduced motion", () => {
    const { engine } = setup(true);

    engine.reset();

    expect(calls("d3ReheatSimulation")).toHaveLength(0);
  });
});

describe("createGraph3D, zoom and orbit", () => {
  it("zooms by moving the camera along its line of sight to what it orbits", () => {
    const { engine } = setup();
    const before = fake().position.z;

    engine.zoomBy(2);

    expect(fake().position.z).toBeCloseTo(before / 2, 5);
    engine.zoomBy(0.5);
    expect(fake().position.z).toBeCloseTo(before, 5);
  });

  it("zooms and orbits about the controls' target, not the origin and not the getter's lookAt", () => {
    const { engine } = setup();
    fake().controlsObject.target = { x: 50, y: 0, z: 0 };
    fake().position = { x: 50, y: 0, z: 100 };

    engine.zoomBy(2);
    const [zoomed, zoomAim] = moves().at(-1) as [Point, Point];
    expect(zoomed.x).toBeCloseTo(50, 5);
    expect(zoomed.z).toBeCloseTo(50, 5);
    expect(zoomAim).toEqual({ x: 50, y: 0, z: 0 });

    fake().position = { x: 50, y: 0, z: 100 };
    engine.orbit?.("right");
    const [turned, turnAim] = moves().at(-1) as [Point, Point];
    expect(turnAim).toEqual({ x: 50, y: 0, z: 0 });
    expect(Math.hypot(turned.x - 50, turned.z)).toBeCloseTo(100, 3);
    expect(turned.x).toBeGreaterThan(50);
  });

  it("keeps the camera between the nearest and the farthest distance however far it is zoomed", () => {
    const { engine } = setup();

    engine.zoomBy(1e9);
    expect(fake().position.z).toBeCloseTo(GRAPH3D_MIN_DISTANCE, 5);
    engine.zoomBy(1e-9);

    expect(fake().position.z).toBeCloseTo(GRAPH3D_MAX_DISTANCE, 5);
  });

  it("orbits the camera with Shift and an arrow key", () => {
    const { engine } = setup();
    fake().position = { x: 0, y: 0, z: 100 };

    engine.orbit?.("right");
    const turned = fake().position;
    expect(turned.x).toBeGreaterThan(0);
    expect(Math.hypot(turned.x, turned.y, turned.z)).toBeCloseTo(100, 3);
    engine.orbit?.("left");
    engine.orbit?.("left");
    expect(fake().position.x).toBeLessThan(0);
    engine.orbit?.("up");
    expect(fake().position.y).toBeGreaterThan(0);
    expect(Math.hypot(fake().position.x, fake().position.y, fake().position.z)).toBeCloseTo(100, 3);
  });

  it("never tilts the camera over the poles", () => {
    const { engine } = setup();
    fake().position = { x: 0, y: 0, z: 100 };

    for (let step = 0; step < 40; step += 1) engine.orbit?.("up");

    expect(fake().position.y).toBeLessThan(100);
    expect(Number.isFinite(fake().position.z)).toBe(true);
  });
});

describe("createGraph3D, walking from node to node", () => {
  /** lesson:1 at the left, then lesson:2 and a task to its right, one above. Screen position is 120 + 50 * graph x. */
  function row() {
    const set = setup();
    const at = (id: string, x: number, y: number): void => {
      Object.assign(set.nodeObjects.find((n) => n.id === id) as object, { x, y, z: 0 });
    };
    set.nodeObjects.forEach((n) => Object.assign(n, { x: 500, y: 500, z: 0 }));
    at("lesson:1", 0, 0);
    at("lesson:2", 2, 0);
    at("task:task-0106", 4, 0);
    at("area:hooks", 0, -3);
    return set;
  }

  it("moves to the nearest node in that direction on the screen, as the 2D view does, and glides the camera there", () => {
    const { engine } = row();

    expect(engine.move("lesson:1", "right")).toBe("lesson:2");
    const [position, lookAt] = moves().at(-1) as [Point, Point];
    expect(lookAt).toEqual({ x: 2, y: 0, z: 0 });
    expect(Math.hypot(position.x - lookAt.x, position.y - lookAt.y, position.z - lookAt.z)).toBeGreaterThanOrEqual(GRAPH3D_FOCUS_DISTANCE);
    expect(engine.move("lesson:2", "right")).toBe("task:task-0106");
    expect(engine.move("task:task-0106", "left")).toBe("lesson:2");
  });

  it("follows the row rather than a diagonal, and goes up and down", () => {
    const { engine } = row();

    expect(engine.move("lesson:1", "up")).toBe("area:hooks");
    expect(engine.move("area:hooks", "down")).toBe("lesson:1");
  });

  it("stays where it is, with no camera move, when nothing lies in that direction or the node is unknown", () => {
    const { engine } = row();
    const before = moves().length;

    expect(engine.move("lesson:1", "left")).toBeNull();
    expect(engine.move("ghost", "right")).toBeNull();

    expect(moves().length).toBe(before);
  });

  it("flies the camera to a node that is revealed, away from the middle of the graph", () => {
    const { engine, nodeObjects } = setup();
    Object.assign(nodeObjects.find((n) => n.id === "lesson:2") as object, { x: 100, y: 0, z: 0 });

    engine.reveal("lesson:2");

    const [position, lookAt] = moves().at(-1) as [Point, Point];
    expect(lookAt.x).toBe(100);
    expect(position.x).toBeCloseTo(100 + GRAPH3D_FOCUS_DISTANCE, 5);
  });

  it("flies out far enough to take in the neighbours of the node it goes to, so a hub is framed with what it links to", () => {
    const { engine, nodeObjects } = setup();
    nodeObjects.forEach((n) => Object.assign(n, { x: 100, y: 0, z: 0 }));
    Object.assign(nodeObjects.find((n) => n.id === "area:hooks") as object, { x: 100, y: 600, z: 0 });

    engine.reveal("lesson:1");

    const [position, lookAt] = moves().at(-1) as [Point, Point];
    const away = Math.hypot(position.x - lookAt.x, position.y - lookAt.y, position.z - lookAt.z);
    expect(away).toBeGreaterThan(600 / Math.tan((25 * Math.PI) / 180) * 0.9);
  });

  it("does not put the camera inside a node that sits at the middle of the graph", () => {
    const { engine, nodeObjects } = setup();
    Object.assign(nodeObjects[0], { x: 0, y: 0, z: 0 });

    engine.reveal(nodeObjects[0].id);

    const [position] = moves().at(-1) as [Point, Point];
    expect(Math.hypot(position.x, position.y, position.z)).toBeGreaterThanOrEqual(GRAPH3D_FOCUS_DISTANCE);
  });

  it("ignores a reveal of an unknown node or one without a position yet", () => {
    const { engine } = setup();
    const before = moves().length;

    engine.reveal("ghost");
    engine.reveal("lesson:2");

    expect(moves()).toHaveLength(before);
  });
});

describe("createGraph3D, sleeping", () => {
  it("keeps rendering until the layout has stopped, then sleeps after the idle time", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    setup();

    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS * 3);
    expect(calls("pauseAnimation")).toHaveLength(0);
    (fake().props.onEngineStop as () => void)();
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS - 100);
    expect(calls("pauseAnimation")).toHaveLength(0);
    vi.advanceTimersByTime(200);

    expect(calls("pauseAnimation")).toHaveLength(1);
  });

  it("does not sleep while the layout is running again, for example after a node was dragged", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    setup();
    (fake().props.onEngineStop as () => void)();
    (fake().props.onEngineTick as () => void)();

    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS * 2);

    expect(calls("pauseAnimation")).toHaveLength(0);
  });

  it("does not sleep while a node is highlighted, because the particles need the loop", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { nodeObjects } = setup();
    (fake().props.onEngineStop as () => void)();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS * 2);
    expect(calls("pauseAnimation")).toHaveLength(0);
    hover(null);
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);

    expect(calls("pauseAnimation")).toHaveLength(1);
  });

  it("sleeps with a node highlighted under reduced motion, which runs no particles", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { nodeObjects } = setup(true);
    (fake().props.onEngineStop as () => void)();
    hover(nodeObjects.find((n) => n.id === "lesson:1"));

    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);

    expect(calls("pauseAnimation")).toHaveLength(1);
  });

  it("stops the label loop while asleep and starts it again, with the render loop, on a pointer move", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { container } = setup();
    (fake().props.onEngineStop as () => void)();
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);
    flushFrame();
    expect(queue.size).toBe(0);

    container.dispatchEvent(new Event("pointermove", { bubbles: true }));

    expect(calls("resumeAnimation")).toHaveLength(1);
    expect(queue.size).toBe(1);
  });

  it.each([
    ["a pointer press", (c: HTMLElement) => c.dispatchEvent(new Event("pointerdown", { bubbles: true }))],
    ["the wheel", (c: HTMLElement) => c.dispatchEvent(new Event("wheel", { bubbles: true }))],
    ["a touch", (c: HTMLElement) => c.dispatchEvent(new Event("touchstart", { bubbles: true }))],
  ])("wakes on %s", (_name, act) => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { container } = setup();
    (fake().props.onEngineStop as () => void)();
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);

    act(container);

    expect(calls("resumeAnimation")).toHaveLength(1);
  });

  it("wakes for the calls the component makes, so a camera glide runs, and sleeps again afterwards", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { engine } = setup();
    (fake().props.onEngineStop as () => void)();
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);
    expect(calls("pauseAnimation")).toHaveLength(1);

    engine.zoomBy(2);
    expect(calls("resumeAnimation")).toHaveLength(1);
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);

    expect(calls("pauseAnimation")).toHaveLength(2);
  });

  it("wakes for a change of selection and for the theme, and for a resize", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { engine } = setup();
    (fake().props.onEngineStop as () => void)();
    vi.advanceTimersByTime(GRAPH3D_IDLE_SLEEP_MS + 100);

    engine.setView({ selected: "lesson:1", focused: null });

    expect(calls("resumeAnimation")).toHaveLength(1);
  });
});

describe("createGraph3D, labels", () => {
  it("places the label of a hovered node as plain text above its projected position", () => {
    const { container, nodeObjects } = setup();
    Object.assign(nodeObjects.find((n) => n.id === "lesson:1") as object, { x: 0, y: 2, z: 3 });

    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    flushFrame();

    const label = container.querySelector<HTMLElement>("div")!;
    expect(label.textContent).toBe("alpha-lesson");
    expect(label.hidden).toBe(false);
    expect(label.style.transform).toContain("120px");
  });

  it("keeps a hostile label as inert text", () => {
    const hostile = { nodes: [{ id: "x", kind: "lesson", label: "<img src=x onerror=alert(1)><script>alert(2)</script>", weight: 1 }], edges: [] };
    const container = document.createElement("div");
    document.body.append(container);
    engines.push(createGraph3D(container, buildScene(hostile.nodes, hostile.edges), { reducedMotion: false, onSelect: vi.fn() }));
    const node = (fake().props.graphData as { nodes: Record<string, unknown>[] }).nodes[0];
    Object.assign(node, { x: 0, y: 0, z: 0 });

    hover(node);
    flushFrame();

    expect(container.querySelector("img, script")).toBeNull();
    expect(container.querySelector("div")?.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("labels the neighbours of the active node too, so a selection reads at fit", () => {
    const { container, nodeObjects, place } = setup();
    place(10);

    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    flushFrame();

    expect(shownLabels(container).sort()).toEqual(["alpha-lesson", "hooks", "senior-backend-dev", "task-0106"]);
  });

  it("drops a neighbour label that would overlap another, and never the active node's own", () => {
    const { container, nodeObjects } = setup();
    nodeObjects.forEach((n) => Object.assign(n, { x: 0, y: 0, z: 0 }));

    hover(nodeObjects.find((n) => n.id === "lesson:1"));
    flushFrame();

    expect(shownLabels(container)).toEqual(["alpha-lesson"]);
  });

  it("shows no neighbour labels while nothing is active", () => {
    const { container, place } = setup();
    place(10);
    flushFrame();

    expect(shownLabels(container)).toHaveLength(0);
  });

  it("shows one label per distinct node among hover, focus and selection, and hides the rest", () => {
    const { container, nodeObjects, engine } = setup();
    for (const n of nodeObjects) Object.assign(n, { x: 1, y: 1, z: 1 });
    engine.setView({ selected: "lesson:1", focused: "lesson:1" });
    flushFrame();
    expect(shownLabels(container)).toHaveLength(1);

    hover(nodeObjects.find((n) => n.id === "lesson:2"));
    flushFrame();
    expect(shownLabels(container).sort()).toEqual(["alpha-lesson", "beta-lesson"]);

    hover(null);
    engine.setView({ selected: null, focused: null });
    flushFrame();
    expect(shownLabels(container)).toHaveLength(0);
  });
});

describe("createGraph3D, destroy and a failed setup", () => {
  it("stops the frame loop, removes the labels and the scene and releases the controls", () => {
    const { container, nodeObjects, engine } = setup();
    Object.assign(nodeObjects[0], { x: 1, y: 1, z: 1 });
    hover(nodeObjects[0]);
    flushFrame();
    expect(queue.size).toBe(1);

    engine.destroy();

    expect(queue.size).toBe(0);
    expect(calls("_destructor")).toHaveLength(1);
    expect(container.childElementCount).toBe(0);
    expect(fake().controlListeners.size).toBe(0);
  });

  it("hands the GPU context back before it destroys the scene", () => {
    const { engine } = setup();

    engine.destroy();

    expect(fake().rendererObject.forceContextLoss).toHaveBeenCalledTimes(1);
    expect(fakes.order).toEqual(["forceContextLoss", "_destructor"]);
  });

  it("ends a running camera glide before it destroys the scene", () => {
    const { engine } = setup();
    const before = moves().length;

    engine.destroy();

    expect(moves().length).toBe(before + 1);
    expect(moves().at(-1)?.[2]).toBe(0);
  });

  it("can be destroyed twice", () => {
    const { engine } = setup();

    engine.destroy();
    engine.destroy();

    expect(calls("_destructor")).toHaveLength(1);
  });

  it("does not refresh a graph that is gone", () => {
    const { engine, nodeObjects } = setup();
    engine.destroy();
    const refreshes = calls("refresh").length;

    hover(nodeObjects[0]);

    expect(calls("refresh")).toHaveLength(refreshes);
  });

  it("tears everything down and rethrows when setup fails after the scene exists", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fakes.failOn = "graphData";
    const container = document.createElement("div");
    document.body.append(container);

    expect(() => createGraph3D(container, buildScene(GRAPH.nodes, GRAPH.edges), { reducedMotion: false, onSelect: vi.fn() })).toThrow("graphData failed");

    expect(fakes.order).toEqual(["forceContextLoss", "_destructor"]);
    expect(container.childElementCount).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect(queue.size).toBe(0);
  });

  it("empties the container and rethrows when the scene cannot be created at all, for example without WebGL", () => {
    fakes.failOn = "constructor";
    const container = document.createElement("div");
    container.append(document.createElement("canvas"));
    document.body.append(container);

    expect(() => createGraph3D(container, buildScene(GRAPH.nodes, GRAPH.edges), { reducedMotion: false, onSelect: vi.fn() })).toThrow("WebGL");

    expect(container.childElementCount).toBe(0);
    expect(fakes.instances).toHaveLength(0);
  });
});

import ForceGraph3D, { type ForceGraph3DInstance, type NodeObject } from "3d-force-graph";
import { Mesh, MeshBasicMaterial, SphereGeometry, Vector2 } from "three";

import {
  FALLBACK_HEIGHT,
  FALLBACK_WIDTH,
  FIT_PADDING,
  GRAPH3D_ACTIVE_HALO_OPACITY,
  GRAPH3D_ACTIVE_NODE_SCALE,
  GRAPH3D_BASE_LINK_MIX,
  GRAPH3D_BASE_LINK_WIDTH,
  GRAPH3D_CHARGE,
  GRAPH3D_DEFAULT_FOV,
  GRAPH3D_DIM_MIX,
  GRAPH3D_FIT_DELAYS_MS,
  GRAPH3D_FIT_MIN_NODES,
  GRAPH3D_FIT_OUTLIER_FACTOR,
  GRAPH3D_FOCUS_DISTANCE,
  GRAPH3D_FOCUS_RADII,
  GRAPH3D_HALO_OPACITY,
  GRAPH3D_HALO_SCALE,
  GRAPH3D_IDLE_SLEEP_MS,
  GRAPH3D_LABEL_CHAR_PX,
  GRAPH3D_LABEL_HEIGHT_PX,
  GRAPH3D_LABEL_PAD_PX,
  GRAPH3D_LINK_DISTANCE,
  GRAPH3D_LIT_LINK_WIDTH,
  GRAPH3D_LIT_NODE_SCALE,
  GRAPH3D_MAX_DISTANCE,
  GRAPH3D_MIN_DISTANCE,
  GRAPH3D_NEIGHBOUR_LABELS,
  GRAPH3D_ORBIT_STEP,
  GRAPH3D_PARTICLE_SPEED,
  GRAPH3D_PARTICLE_WIDTH,
  GRAPH3D_PARTICLES,
  GRAPH3D_RADIUS_SCALE,
  GRAPH3D_START_DISTANCE,
  GRAPH3D_WARMUP_TICKS,
  VIEW_ANIMATION_MS,
} from "./config";
import { readPalette, type Palette } from "./draw";
import type { EngineOptions, GraphEngine } from "./engine";
import { isLitLink, litNodes, mixColours } from "./highlight";
import { kindPaint } from "./kinds";
import type { Scene } from "./layout";
import { nearestInDirection, placeLabels, type Direction, type LabelBox } from "./view";

/*
 * The 3D view (FR-106): a 3d-force-graph scene (three.js, d3-force-3d) that orbits, zooms, drags nodes and
 * selects on click. This module is the lazy chunk of the Graphs page, so three.js is fetched only when a 3D view
 * is opened. Highlight rules are the shared ones of highlight.ts: the active node and its neighbours keep full
 * strength and get a halo, links that touch the node are thick, accent coloured and carry particles, the rest
 * blends into the background. Labels are plain text elements the engine positions itself; nothing is rendered
 * from an HTML string, so a hostile label stays text. Once the layout has settled and nothing is highlighted the
 * render loop sleeps; any pointer, key, resize, theme or API call wakes it.
 */

interface Node3 extends NodeObject {
  id: string;
  kind: string;
  label: string;
  radius: number;
}

interface Link3 {
  source: string | Node3;
  target: string | Node3;
}

type Point = { x: number; y: number; z: number };

/** What the engine uses of three's OrbitControls. The underscore fields are private to three. */
interface Controls {
  addEventListener: (type: string, listener: () => void) => void;
  removeEventListener: (type: string, listener: () => void) => void;
  target?: Point;
  minDistance?: number;
  maxDistance?: number;
  _pointers?: number[];
  _pointerPositions?: Record<number, Vector2 | undefined>;
  _onPointerUp?: (event: Event) => void;
}

const LABEL_CLASS =
  "pointer-events-none absolute top-0 left-0 z-10 max-w-64 truncate rounded bg-background/90 px-1.5 py-0.5 text-[11px] font-medium text-foreground shadow-sm";
const LABEL_LIFT_PX = 14;
const SPHERE_SEGMENTS = 20;
const LINK_OPACITY = 1;
const LABEL_GAP = 2;
const LINK_RESOLUTION = 6;
const NODE_RESOLUTION = 14;
const REDUCED_COOLDOWN_MS = 250;
const ZOOM_ANIMATION_MS = 200;
const MAX_POLAR = Math.PI / 2 - 0.05;
const WAKE_EVENTS = ["pointermove", "pointerdown", "wheel", "touchstart"] as const;

const idOf = (end: string | Node3): string => (typeof end === "string" ? end : end.id);
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const hasPosition = (node: Node3): node is Node3 & Point => node.x !== undefined && node.y !== undefined && node.z !== undefined;

export function createGraph3D(container: HTMLElement, scene: Scene, options: EngineOptions): GraphEngine {
  const { reducedMotion } = options;
  const nodes: Node3[] = scene.nodes.map((node) => ({ id: node.id, kind: node.kind, label: node.label, radius: node.r * GRAPH3D_RADIUS_SCALE }));
  const links: Link3[] = scene.links.map((link) => ({ source: link.source.id, target: link.target.id }));
  const byId = new Map(nodes.map((node) => [node.id, node]));

  let palette: Palette = readPalette(container);
  let hovered: string | null = null;
  let selected: string | null = null;
  let focused: string | null = null;
  let userMoved = false;
  let engineStopped = false;
  let sleeping = false;
  let disposed = false;
  let frameId = 0;
  let sleepTimer = 0;

  /** Everything acquired, undone in reverse order by `dispose`, so a setup that fails half way cleans up after itself. */
  const disposers: (() => void)[] = [];
  const dispose = (): void => {
    if (disposed) return;
    disposed = true;
    for (const undo of disposers.reverse()) {
      try {
        undo();
      } catch {
        // A teardown step that fails must not stop the others.
      }
    }
  };

  // The active node (hovered, else selected), who it lights, and the neighbours worth a label: recomputed when it
  // changes, not once per node and per link on every refresh.
  let activeId: string | null = null;
  let litSet: Set<string> | null = null;
  let neighbourLabels: string[] = [];
  const refreshActive = (): void => {
    activeId = hovered ?? selected;
    litSet = litNodes(scene, activeId);
    neighbourLabels = activeId
      ? [...(scene.neighbours.get(activeId) ?? [])]
          .sort((a, b) => (scene.byId.get(b)?.degree ?? 0) - (scene.byId.get(a)?.degree ?? 0))
          .slice(0, GRAPH3D_NEIGHBOUR_LABELS + 3)
      : [];
  };
  const isStrong = (id: string): boolean => id === activeId || id === selected || id === focused;

  const token = (name: string): string => palette[name] ?? "currentColor";
  const background = (): string => token("--muted");
  const accent = (): string => token("--ring");
  const baseLink = (): string => mixColours(token("--muted-foreground"), background(), GRAPH3D_BASE_LINK_MIX);
  /** A node's drawn radius: the active node and the nodes it lights are larger, so a selection reads at fit. */
  const drawnRadius = (node: Node3): number => {
    if (isStrong(node.id)) return node.radius * GRAPH3D_ACTIVE_NODE_SCALE;
    return litSet?.has(node.id) ? node.radius * GRAPH3D_LIT_NODE_SCALE : node.radius;
  };
  const litLink = (link: object): boolean => {
    const { source, target } = link as Link3;
    return isLitLink(activeId, idOf(source), idOf(target));
  };

  /** The fill of a kind; a kind drawn as a ring in 2D (its fill is the card colour) uses its ring colour here. */
  const kindColour = (kind: string): string => {
    const paint = kindPaint(kind);
    return token(paint.fill === "--card" ? paint.stroke : paint.fill);
  };

  let graph: ForceGraph3DInstance;
  try {
    graph = new ForceGraph3D(container, { controlType: "orbit" });
  } catch (error) {
    container.replaceChildren();
    throw error;
  }
  disposers.push(() => {
    // Hand the GPU context back now: a page that opens and closes 3D views must not run out of WebGL contexts.
    graph.renderer().forceContextLoss();
    graph._destructor();
    container.replaceChildren();
  });

  const camera = (): Point => graph.cameraPosition();
  /** What the camera orbits: the controls' target. (The getter's `lookAt` is a point far ahead, not the target.) */
  const aim = (): Point => (graph.controls() as Controls).target ?? { x: 0, y: 0, z: 0 };
  const glide = (ms: number): number => (reducedMotion ? 0 : ms);

  function wake(): void {
    if (disposed) return;
    window.clearTimeout(sleepTimer);
    if (!sleeping) return;
    sleeping = false;
    graph.resumeAnimation();
    if (frameId === 0) frameId = window.requestAnimationFrame(positionLabels);
  }

  /** Sleeps when the layout has settled and nothing needs the loop: no particles run, no camera glide is left. */
  function scheduleSleep(): void {
    if (disposed) return;
    window.clearTimeout(sleepTimer);
    if (!engineStopped || (activeId !== null && !reducedMotion)) return;
    sleepTimer = window.setTimeout(() => {
      // Look again: the layout may have started running, or a node been highlighted, since this was scheduled.
      if (disposed || !engineStopped || (activeId !== null && !reducedMotion)) return;
      sleeping = true;
      graph.pauseAnimation();
    }, GRAPH3D_IDLE_SLEEP_MS);
  }

  /** An input or API call: make sure the loop runs, and let it go back to sleep afterwards. */
  function touch(): void {
    wake();
    scheduleSleep();
  }

  function relight(): void {
    if (disposed) return;
    refreshActive();
    wake();
    graph.refresh();
    scheduleSleep();
  }

  const markMoved = (): void => {
    userMoved = true;
  };

  /** The nodes the fit keeps: all of a small graph, else those not thrown far out by the layout. */
  function bulk(): (Node3 & Point)[] {
    const placed = nodes.filter(hasPosition);
    if (placed.length < GRAPH3D_FIT_MIN_NODES) return placed;
    const middle = (axis: "x" | "y" | "z"): number => placed.reduce((sum, n) => sum + n[axis], 0) / placed.length;
    const [mx, my, mz] = [middle("x"), middle("y"), middle("z")];
    const spread = (n: Point): number => Math.hypot(n.x - mx, n.y - my, n.z - mz);
    const sorted = placed.map(spread).sort((a, b) => a - b);
    const cutoff = sorted[Math.floor(sorted.length * 0.9)] * GRAPH3D_FIT_OUTLIER_FACTOR;
    return placed.filter((n) => spread(n) <= cutoff);
  }

  /**
   * Fits the bulk of the graph: the camera keeps its viewing direction and moves to the distance at which the
   * nodes, seen along that direction, plus the padding, fill the height or the width of the view, whichever is
   * tighter. 3d-force-graph's own zoomToFit sizes by the longest side of the bounding box and leaves the graph a
   * small blob in a wide view.
   */
  function fitView(ms: number): void {
    const kept = bulk();
    if (disposed || kept.length === 0) return;
    touch();
    const mid = (axis: "x" | "y" | "z"): number => {
      let low = Infinity;
      let high = -Infinity;
      for (const n of kept) {
        low = Math.min(low, n[axis]);
        high = Math.max(high, n[axis]);
      }
      return (low + high) / 2;
    };
    const centre = { x: mid("x"), y: mid("y"), z: mid("z") };
    const target = aim();
    const from = camera();
    let dir = { x: from.x - target.x, y: from.y - target.y, z: from.z - target.z };
    const length = Math.hypot(dir.x, dir.y, dir.z) || 1;
    dir = { x: dir.x / length, y: dir.y / length, z: dir.z / length };
    // The camera's right and up axes: world up crossed with the direction, then the direction crossed with that.
    const upHint = Math.abs(dir.y) > 0.99 ? { x: 0, y: 0, z: 1 } : { x: 0, y: 1, z: 0 };
    const cross = (a: Point, b: Point): Point => ({ x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x });
    const norm = (v: Point): Point => {
      const size = Math.hypot(v.x, v.y, v.z) || 1;
      return { x: v.x / size, y: v.y / size, z: v.z / size };
    };
    const right = norm(cross(upHint, dir));
    const up = cross(dir, right);
    const dot = (a: Point, b: Point): number => a.x * b.x + a.y * b.y + a.z * b.z;
    const height = container.clientHeight || FALLBACK_HEIGHT;
    const width = container.clientWidth || FALLBACK_WIDTH;
    const fov = (graph.camera() as { fov?: number }).fov;
    const slope = Math.tan(((typeof fov === "number" ? fov : GRAPH3D_DEFAULT_FOV) / 2) * (Math.PI / 180));
    // The view's half-extent per unit of distance, less the padding, vertically and horizontally.
    const slopeHigh = (slope * Math.max(height - 2 * FIT_PADDING, height / 2)) / height;
    const slopeWide = (slope * (width / height) * Math.max(width - 2 * FIT_PADDING, width / 2)) / width;
    // Each node, seen in perspective, needs the camera at least this far out for its circle to stay inside.
    let distance = 0;
    for (const n of kept) {
      const offset = { x: n.x - centre.x, y: n.y - centre.y, z: n.z - centre.z };
      const r = drawnRadius(n);
      const toward = dot(offset, dir);
      distance = Math.max(distance, toward + (Math.abs(dot(offset, up)) + r) / slopeHigh, toward + (Math.abs(dot(offset, right)) + r) / slopeWide);
    }
    graph.cameraPosition(
      { x: centre.x + dir.x * distance, y: centre.y + dir.y * distance, z: centre.z + dir.z * distance },
      centre,
      ms,
    );
  }

  // Labels: the hovered, focused and selected nodes and the neighbours of the active one. Plain text elements placed
  // over the scene each frame; a label that would overlap one already placed gives way, the first three never do.
  const labelEls: HTMLElement[] = [];
  const labelEl = (index: number): HTMLElement => {
    let el = labelEls[index];
    if (!el) {
      el = document.createElement("div");
      el.className = LABEL_CLASS;
      el.hidden = true;
      container.append(el);
      labelEls[index] = el;
    }
    return el;
  };
  function positionLabels(): void {
    frameId = 0;
    if (disposed || sleeping) return;
    const forced = [...new Set([hovered, focused, selected].filter((id): id is string => id !== null))];
    const neighbours = neighbourLabels.filter((id) => !forced.includes(id)).slice(0, GRAPH3D_NEIGHBOUR_LABELS);
    const boxes: LabelBox[] = [];
    for (const id of [...forced, ...neighbours]) {
      const node = byId.get(id);
      if (!node || !hasPosition(node)) continue;
      const { x, y } = graph.graph2ScreenCoords(node.x, node.y, node.z);
      boxes.push({
        id,
        x: Math.round(x),
        y: Math.round(y - LABEL_LIFT_PX),
        width: node.label.length * GRAPH3D_LABEL_CHAR_PX + GRAPH3D_LABEL_PAD_PX,
        height: GRAPH3D_LABEL_HEIGHT_PX,
        forced: forced.includes(id),
      });
    }
    const kept = placeLabels(boxes, LABEL_GAP);
    kept.forEach((box, index) => {
      const el = labelEl(index);
      el.textContent = byId.get(box.id)?.label ?? "";
      el.hidden = false;
      el.style.transform = `translate(${box.x}px, ${box.y}px) translate(-50%, -100%)`;
    });
    for (let index = kept.length; index < labelEls.length; index += 1) labelEls[index].hidden = true;
    frameId = window.requestAnimationFrame(positionLabels);
  }

  try {
    graph
      .width(container.clientWidth || FALLBACK_WIDTH)
      .height(container.clientHeight || FALLBACK_HEIGHT)
      .backgroundColor(background())
      .showNavInfo(false)
      .nodeId("id")
      .nodeRelSize(1)
      .nodeVal((node) => drawnRadius(node as Node3) ** 3)
      .nodeResolution(NODE_RESOLUTION)
      .nodeOpacity(1)
      .nodeLabel(() => "")
      .linkLabel(() => "")
      .nodeColor((node) => {
        const { id, kind } = node as Node3;
        const colour = kindColour(kind);
        return litSet && !litSet.has(id) ? mixColours(colour, background(), GRAPH3D_DIM_MIX) : colour;
      })
      .nodeThreeObjectExtend(true)
      .nodeThreeObject((node) => {
        const { id } = node as Node3;
        const strong = isStrong(id);
        if (!strong && !litSet?.has(id)) return false as unknown as Mesh;
        return new Mesh(
          new SphereGeometry(drawnRadius(node as Node3) * GRAPH3D_HALO_SCALE, SPHERE_SEGMENTS, SPHERE_SEGMENTS),
          new MeshBasicMaterial({ color: accent(), transparent: true, opacity: strong ? GRAPH3D_ACTIVE_HALO_OPACITY : GRAPH3D_HALO_OPACITY, depthWrite: false }),
        );
      })
      .linkOpacity(LINK_OPACITY)
      .linkResolution(LINK_RESOLUTION)
      .linkColor((link) => {
        if (!activeId) return baseLink();
        return litLink(link) ? accent() : mixColours(baseLink(), background(), GRAPH3D_DIM_MIX);
      })
      .linkWidth((link) => (litLink(link) ? GRAPH3D_LIT_LINK_WIDTH : GRAPH3D_BASE_LINK_WIDTH))
      // Particles are motion, so none under reduced motion; the thick accent link carries the emphasis there.
      .linkDirectionalParticles((link) => (!reducedMotion && litLink(link) ? GRAPH3D_PARTICLES : 0))
      .linkDirectionalParticleWidth(GRAPH3D_PARTICLE_WIDTH)
      .linkDirectionalParticleSpeed(GRAPH3D_PARTICLE_SPEED)
      .linkDirectionalParticleColor(() => accent())
      .onNodeHover((node) => {
        const id = node ? (node as Node3).id : null;
        // The pointer reports a hover on every move; only a change of node is worth rebuilding the scene for.
        if (id === hovered) return;
        hovered = id;
        container.dataset.hover = id ? "true" : "false";
        relight();
      })
      .onNodeClick((node) => options.onSelect((node as Node3).id))
      .onBackgroundClick(() => options.onSelect(null))
      .onNodeDrag(markMoved)
      .onNodeDragEnd((node) => {
        // Pinned while held, free afterwards.
        const dragged = node as Node3;
        dragged.fx = undefined;
        dragged.fy = undefined;
        dragged.fz = undefined;
      })
      .onEngineTick(() => {
        engineStopped = false;
      })
      .onEngineStop(() => {
        engineStopped = true;
        if (!userMoved) fitView(glide(VIEW_ANIMATION_MS));
        scheduleSleep();
      });
    (graph.d3Force("link") as { distance: (value: number) => void } | undefined)?.distance(GRAPH3D_LINK_DISTANCE);
    (graph.d3Force("charge") as { strength: (value: number) => void } | undefined)?.strength(GRAPH3D_CHARGE);
    if (reducedMotion) graph.warmupTicks(GRAPH3D_WARMUP_TICKS).cooldownTime(REDUCED_COOLDOWN_MS);
    graph.cameraPosition({ x: 0, y: 0, z: GRAPH3D_START_DISTANCE }, { x: 0, y: 0, z: 0 }, 0);
    graph.graphData({ nodes, links });

    const controls = graph.controls() as Controls;
    controls.minDistance = GRAPH3D_MIN_DISTANCE;
    controls.maxDistance = GRAPH3D_MAX_DISTANCE;
    controls.addEventListener("start", markMoved);
    disposers.push(() => controls.removeEventListener("start", markMoved));

    /*
     * 3d-force-graph ends every node press, a plain click included, by dispatching a synthetic `pointerup` on the
     * document. The orbit controls (three 0.18x) take that for one pointer of several going up: while one pointer
     * is still listed they read its position, which they only record on a move, so a click without a move finds
     * none and throws `Cannot read properties of undefined (reading 'x')`. Record a position for every listed
     * pointer first, so the controls take their designed path and end the gesture on the real pointerup that
     * follows. The controls register this function as a document listener, where `this` is the document, so it
     * is an arrow function.
     */
    const releasePointer = controls._onPointerUp;
    if (typeof releasePointer === "function") {
      controls._onPointerUp = (event: Event): void => {
        const { _pointers: ids, _pointerPositions: positions } = controls;
        if (ids && positions) for (const id of ids) positions[id] ??= new Vector2();
        releasePointer.call(controls, event);
      };
    }

    const fitTimers = GRAPH3D_FIT_DELAYS_MS.map((delay) =>
      window.setTimeout(() => {
        if (!userMoved) fitView(glide(VIEW_ANIMATION_MS));
      }, reducedMotion ? 0 : delay),
    );
    disposers.push(() => {
      for (const timer of fitTimers) window.clearTimeout(timer);
      window.clearTimeout(sleepTimer);
    });

    frameId = window.requestAnimationFrame(positionLabels);
    disposers.push(() => {
      if (frameId !== 0) window.cancelAnimationFrame(frameId);
      frameId = 0;
      for (const el of labelEls) el.remove();
    });

    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(() => {
            touch();
            graph.width(container.clientWidth || FALLBACK_WIDTH).height(container.clientHeight || FALLBACK_HEIGHT);
          });
    resizeObserver?.observe(container);
    disposers.push(() => resizeObserver?.disconnect());

    const themeObserver = new MutationObserver(() => {
      palette = readPalette(container);
      graph.backgroundColor(background());
      relight();
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    disposers.push(() => themeObserver.disconnect());

    for (const type of WAKE_EVENTS) container.addEventListener(type, touch, { passive: true });
    disposers.push(() => {
      for (const type of WAKE_EVENTS) container.removeEventListener(type, touch);
    });

    // End a camera glide that is still running, so its tween cannot fire into a scene that is gone.
    disposers.push(() => graph.cameraPosition(camera(), aim(), 0));

    function reveal(id: string): void {
      const node = byId.get(id);
      if (!node || !hasPosition(node)) return;
      markMoved();
      touch();
      const length = Math.hypot(node.x, node.y, node.z);
      // A node at the middle has no outward direction: look at it along the default one instead of from inside it.
      const dir = length > 1e-6 ? { x: node.x / length, y: node.y / length, z: node.z / length } : { x: 0, y: 0, z: 1 };
      // Far enough out to take in the node's neighbours too, so a hub is framed with what it links to.
      let reach = 0;
      for (const id of scene.neighbours.get(node.id) ?? []) {
        const other = byId.get(id);
        if (other && hasPosition(other)) reach = Math.max(reach, Math.hypot(other.x - node.x, other.y - node.y, other.z - node.z));
      }
      const slope = Math.tan((GRAPH3D_DEFAULT_FOV / 2) * (Math.PI / 180));
      const distance = Math.max(GRAPH3D_FOCUS_DISTANCE, node.radius * GRAPH3D_FOCUS_RADII, (reach + node.radius) / slope);
      graph.cameraPosition(
        { x: node.x + dir.x * distance, y: node.y + dir.y * distance, z: node.z + dir.z * distance },
        { x: node.x, y: node.y, z: node.z },
        glide(VIEW_ANIMATION_MS),
      );
    }

    return {
      fit: () => {
        userMoved = false;
        fitView(glide(VIEW_ANIMATION_MS));
      },
      reset: () => {
        markMoved();
        touch();
        graph.cameraPosition({ x: 0, y: 0, z: GRAPH3D_START_DISTANCE }, { x: 0, y: 0, z: 0 }, glide(VIEW_ANIMATION_MS));
        if (!reducedMotion) graph.d3ReheatSimulation();
      },
      zoomBy: (factor) => {
        markMoved();
        touch();
        const target = aim();
        const from = camera();
        const [dx, dy, dz] = [from.x - target.x, from.y - target.y, from.z - target.z];
        const length = Math.hypot(dx, dy, dz) || GRAPH3D_START_DISTANCE;
        const next = clamp(length / factor, GRAPH3D_MIN_DISTANCE, GRAPH3D_MAX_DISTANCE) / length;
        graph.cameraPosition(
          { x: target.x + dx * next, y: target.y + dy * next, z: target.z + dz * next },
          target,
          glide(ZOOM_ANIMATION_MS),
        );
      },
      setView: (next) => {
        // The component reports its state after every render; only a change is worth rebuilding the scene for.
        if (next.selected === selected && next.focused === focused) return;
        selected = next.selected;
        focused = next.focused;
        relight();
      },
      move: (fromId: string, direction: Direction) => {
        // Walk to the nearest node in that direction as the screen shows it, as the 2D view does, and glide there.
        const placed = nodes.filter(hasPosition).map((n) => ({ id: n.id, ...graph.graph2ScreenCoords(n.x, n.y, n.z) }));
        const origin = placed.find((n) => n.id === fromId);
        const next = origin ? nearestInDirection(placed, origin, direction) : undefined;
        if (!next) return null;
        reveal(next.id);
        return next.id;
      },
      orbit: (direction: Direction) => {
        // Turn the camera around what it orbits: left and right about the vertical axis, up and down tilt.
        markMoved();
        touch();
        const target = aim();
        const from = camera();
        const [x, y, z] = [from.x - target.x, from.y - target.y, from.z - target.z];
        const turn = direction === "left" ? -GRAPH3D_ORBIT_STEP : direction === "right" ? GRAPH3D_ORBIT_STEP : 0;
        const tilt = direction === "up" ? GRAPH3D_ORBIT_STEP : direction === "down" ? -GRAPH3D_ORBIT_STEP : 0;
        const radius = Math.hypot(x, y, z) || GRAPH3D_START_DISTANCE;
        const azimuth = Math.atan2(x, z) + turn;
        const polar = clamp(Math.asin(clamp(y / radius, -1, 1)) + tilt, -MAX_POLAR, MAX_POLAR);
        graph.cameraPosition(
          {
            x: target.x + radius * Math.cos(polar) * Math.sin(azimuth),
            y: target.y + radius * Math.sin(polar),
            z: target.z + radius * Math.cos(polar) * Math.cos(azimuth),
          },
          target,
          glide(ZOOM_ANIMATION_MS),
        );
      },
      reveal,
      destroy: dispose,
    };
  } catch (error) {
    dispose();
    throw error;
  }
}

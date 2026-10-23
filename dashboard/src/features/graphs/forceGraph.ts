import { drag, type D3DragEvent } from "d3-drag";
import { pointer, select } from "d3-selection";
import { zoom, zoomIdentity, zoomTransform, type D3ZoomEvent } from "d3-zoom";

import {
  DRAG_ALPHA,
  EARLY_FIT_ALPHA,
  FALLBACK_HEIGHT,
  FALLBACK_WIDTH,
  RESET_ALPHA,
  TICKS_PER_FRAME,
  VIEW_ANIMATION_MS,
  ZOOM_MAX,
  ZOOM_MIN,
} from "./config";
import { paint, readFontFamily, readPalette } from "./draw";
import type { EngineOptions, GraphEngine } from "./engine";
import { createSimulation, settle, type Scene, type SimNode } from "./layout";
import {
  centredTransform,
  clampScale,
  fitTransform,
  inView,
  nearestInDirection,
  nodeAtPoint,
  toGraph,
  toScreen,
  type Size,
  type ViewTransform,
} from "./view";

export interface Engine2D extends GraphEngine {
  transform: () => ViewTransform;
  isAnimating: () => boolean;
}

/** What d3-drag carries for a gesture that started on a node: the node's screen position and the node. */
interface Held {
  x: number;
  y: number;
  node: SimNode;
}

interface Tween {
  from: ViewTransform;
  to: ViewTransform;
  startedAt: number | null;
}

const easeOut = (p: number): number => 1 - (1 - p) ** 3;

/**
 * The live graph (FR-106): a d3-force simulation ticked from one animation-frame loop, drawn on a canvas, with
 * d3-zoom for wheel, pinch and background pan and d3-drag for moving a node (the node is pinned while held and
 * the layout reheats). The loop runs only while something moves, so an idle graph costs nothing. With reduced
 * motion the layout is settled before the first frame and views change without animation.
 */
export function createGraphEngine(canvas: HTMLCanvasElement, scene: Scene, options: EngineOptions): Engine2D {
  const context = canvas.getContext("2d");
  const simulation = createSimulation(scene);
  const view = select(canvas);
  const { reducedMotion } = options;

  let size: Size = { width: FALLBACK_WIDTH, height: FALLBACK_HEIGHT };
  let ratio = 1;
  let palette = readPalette(canvas);
  let fontFamily = readFontFamily(canvas);
  let hovered: SimNode | undefined;
  let held: SimNode | undefined;
  let selected: SimNode | undefined;
  let focused: SimNode | undefined;
  let userMoved = false;
  let earlyFitDone = false;
  let restFitDone = false;
  let tween: Tween | null = null;
  let frameId = 0;
  let inFrame = false;
  let dirty = true;
  let destroyed = false;

  const current = (): ViewTransform => zoomTransform(canvas);
  const animating = (): boolean => !reducedMotion && (simulation.alpha() >= simulation.alphaMin() || simulation.alphaTarget() > 0);

  function measure(): void {
    ratio = window.devicePixelRatio || 1;
    size = { width: canvas.clientWidth || FALLBACK_WIDTH, height: canvas.clientHeight || FALLBACK_HEIGHT };
    canvas.width = Math.round(size.width * ratio);
    canvas.height = Math.round(size.height * ratio);
  }

  function draw(): void {
    if (!context) return;
    paint(context, { size, ratio, transform: current(), palette, fontFamily, scene, active: held ?? hovered ?? selected, selected, focused });
  }

  function schedule(): void {
    if (frameId !== 0 || destroyed || inFrame) return;
    frameId = window.requestAnimationFrame(frame);
  }

  function markDirty(): void {
    dirty = true;
    schedule();
  }

  function stepTween(now: number): void {
    if (!tween) return;
    tween.startedAt ??= now;
    const p = Math.min(1, (now - tween.startedAt) / VIEW_ANIMATION_MS);
    const e = easeOut(p);
    const { from, to } = tween;
    apply({ x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, k: from.k * (to.k / from.k) ** e });
    if (p >= 1) tween = null;
  }

  function frame(now: number): void {
    frameId = 0;
    if (destroyed) return;
    inFrame = true;
    try {
      stepTween(now);
      if (animating()) {
        for (let tick = 0; tick < TICKS_PER_FRAME; tick += 1) simulation.tick();
        dirty = true;
        if (!userMoved && !earlyFitDone && simulation.alpha() < EARLY_FIT_ALPHA) {
          earlyFitDone = true;
          goTo(fitTransform(scene.nodes, size));
        }
      }
      if (!animating() && !userMoved && !restFitDone) {
        restFitDone = true;
        goTo(fitTransform(scene.nodes, size));
      }
      if (dirty) {
        dirty = false;
        draw();
      }
    } finally {
      inFrame = false;
    }
    if (animating() || tween) schedule();
  }

  const zoomer = zoom<HTMLCanvasElement, unknown>()
    .scaleExtent([ZOOM_MIN, ZOOM_MAX])
    .on("zoom", (event: D3ZoomEvent<HTMLCanvasElement, unknown>) => {
      if (event.sourceEvent) {
        userMoved = true;
        tween = null;
      }
      markDirty();
    });

  function apply(target: ViewTransform): void {
    zoomer.transform(view, zoomIdentity.translate(target.x, target.y).scale(clampScale(target.k)));
  }

  function goTo(target: ViewTransform): void {
    if (reducedMotion) {
      tween = null;
      apply(target);
      return;
    }
    tween = { from: current(), to: target, startedAt: null };
    schedule();
  }

  /** Centres the view on a node that is off the canvas. */
  function reveal(node: SimNode): void {
    const t = current();
    const [sx, sy] = toScreen(t, node.x ?? 0, node.y ?? 0);
    if (inView(sx, sy, size, -node.r * t.k - 24)) return;
    userMoved = true;
    goTo({ k: t.k, x: size.width / 2 - (node.x ?? 0) * t.k, y: size.height / 2 - (node.y ?? 0) * t.k });
  }

  const dragger = drag<HTMLCanvasElement, unknown, Held | undefined>()
    .container(canvas)
    .subject((event: { x: number; y: number }) => {
      const node = nodeAtPoint(scene.nodes, current(), event.x, event.y);
      if (!node) return undefined;
      const [x, y] = toScreen(current(), node.x ?? 0, node.y ?? 0);
      return { x, y, node };
    })
    .on("start", (event: D3DragEvent<HTMLCanvasElement, unknown, Held>) => {
      const { node } = event.subject;
      held = node;
      node.fx = node.x;
      node.fy = node.y;
      userMoved = true;
      tween = null;
      if (!reducedMotion) simulation.alphaTarget(DRAG_ALPHA);
      markDirty();
    })
    .on("drag", (event: D3DragEvent<HTMLCanvasElement, unknown, Held>) => {
      const { node } = event.subject;
      const [x, y] = toGraph(current(), event.x, event.y);
      node.fx = x;
      node.fy = y;
      if (reducedMotion) {
        node.x = x;
        node.y = y;
      }
      markDirty();
    })
    .on("end", (event: D3DragEvent<HTMLCanvasElement, unknown, Held>) => {
      const { node } = event.subject;
      node.fx = null;
      node.fy = null;
      held = undefined;
      simulation.alphaTarget(0);
      markDirty();
    });

  const onPointerMove = (event: PointerEvent): void => {
    const [x, y] = pointer(event, canvas);
    const node = nodeAtPoint(scene.nodes, current(), x, y);
    if (node === hovered) return;
    hovered = node;
    canvas.dataset.hover = node ? "true" : "false";
    markDirty();
  };
  const onPointerLeave = (): void => {
    if (!hovered) return;
    hovered = undefined;
    canvas.dataset.hover = "false";
    markDirty();
  };
  const onClick = (event: MouseEvent): void => {
    const [x, y] = pointer(event, canvas);
    options.onSelect(nodeAtPoint(scene.nodes, current(), x, y)?.id ?? null);
  };

  const resizeObserver = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(() => {
    const before = size;
    measure();
    const t = current();
    apply({ k: t.k, x: t.x + (size.width - before.width) / 2, y: t.y + (size.height - before.height) / 2 });
    markDirty();
  });
  const themeObserver = new MutationObserver(() => {
    palette = readPalette(canvas);
    fontFamily = readFontFamily(canvas);
    markDirty();
  });

  // d3-drag first: its mousedown stops the event before d3-zoom's listener on the same element sees it, so a
  // press on a node drags the node and a press on the background pans.
  view.call(dragger).call(zoomer).on("dblclick.zoom", null);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("click", onClick);
  resizeObserver?.observe(canvas);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

  measure();
  apply(centredTransform(size));
  if (reducedMotion) {
    settle(simulation);
    earlyFitDone = true;
    restFitDone = true;
    apply(fitTransform(scene.nodes, size));
  }
  markDirty();

  return {
    fit: () => {
      userMoved = false;
      restFitDone = !animating();
      goTo(fitTransform(scene.nodes, size));
    },
    reset: () => {
      userMoved = true;
      if (!reducedMotion) simulation.alpha(RESET_ALPHA);
      goTo(centredTransform(size));
      markDirty();
    },
    zoomBy: (factor) => {
      const t = current();
      const k = clampScale(t.k * factor);
      const cx = size.width / 2;
      const cy = size.height / 2;
      userMoved = true;
      goTo({ k, x: cx - ((cx - t.x) / t.k) * k, y: cy - ((cy - t.y) / t.k) * k });
    },
    setView: (next) => {
      selected = next.selected ? scene.byId.get(next.selected) : undefined;
      focused = next.focused ? scene.byId.get(next.focused) : undefined;
      markDirty();
    },
    move: (fromId, direction) => {
      const from = scene.byId.get(fromId);
      const next = from ? nearestInDirection(scene.nodes, from, direction) : undefined;
      if (!next) return null;
      reveal(next);
      return next.id;
    },
    reveal: (id) => {
      const node = scene.byId.get(id);
      if (node) reveal(node);
    },
    transform: current,
    isAnimating: () => animating() || tween !== null,
    destroy: () => {
      destroyed = true;
      if (frameId !== 0) window.cancelAnimationFrame(frameId);
      frameId = 0;
      simulation.stop();
      view.on(".drag", null).on(".zoom", null);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("click", onClick);
      resizeObserver?.disconnect();
      themeObserver.disconnect();
    },
  };
}

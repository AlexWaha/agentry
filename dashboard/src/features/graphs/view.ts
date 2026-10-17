import { FIT_MAX_SCALE, FIT_PADDING, HIT_SLACK_PX, LABEL_ZOOM_THRESHOLD, MAX_RADIUS, ZOOM_MAX, ZOOM_MIN } from "./config";
import type { Scene, SimNode } from "./layout";

/** A pan and zoom: a graph point `p` is drawn at `p * k + (x, y)`. */
export interface ViewTransform {
  x: number;
  y: number;
  k: number;
}

export interface Size {
  width: number;
  height: number;
}

export type Direction = "left" | "right" | "up" | "down";

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

export const clampScale = (k: number): number => clamp(k, ZOOM_MIN, ZOOM_MAX);

/** The transform that centres the origin at scale 1: the view a Reset returns to. */
export function centredTransform(size: Size): ViewTransform {
  return { x: size.width / 2, y: size.height / 2, k: 1 };
}

/** The transform that shows every placed node (and its circle) inside the canvas, centred. */
export function fitTransform(nodes: readonly SimNode[], size: Size): ViewTransform {
  const placed = nodes.filter((node) => node.x !== undefined && node.y !== undefined);
  if (placed.length === 0) return centredTransform(size);
  let left = Infinity;
  let right = -Infinity;
  let top = Infinity;
  let bottom = -Infinity;
  for (const node of placed) {
    left = Math.min(left, (node.x ?? 0) - node.r);
    right = Math.max(right, (node.x ?? 0) + node.r);
    top = Math.min(top, (node.y ?? 0) - node.r);
    bottom = Math.max(bottom, (node.y ?? 0) + node.r);
  }
  const width = Math.max(right - left, 1);
  const height = Math.max(bottom - top, 1);
  const k = clamp(
    Math.min((size.width - FIT_PADDING * 2) / width, (size.height - FIT_PADDING * 2) / height),
    ZOOM_MIN,
    Math.min(FIT_MAX_SCALE, ZOOM_MAX),
  );
  return { k, x: size.width / 2 - ((left + right) / 2) * k, y: size.height / 2 - ((top + bottom) / 2) * k };
}

/** A screen point as a graph point. */
export const toGraph = (transform: ViewTransform, x: number, y: number): [number, number] => [
  (x - transform.x) / transform.k,
  (y - transform.y) / transform.k,
];

/** A graph point as a screen point. */
export const toScreen = (transform: ViewTransform, x: number, y: number): [number, number] => [
  x * transform.k + transform.x,
  y * transform.k + transform.y,
];

/** The nearest node whose circle (plus a small slack) holds the screen point, or undefined. */
export function nodeAtPoint(nodes: readonly SimNode[], transform: ViewTransform, x: number, y: number): SimNode | undefined {
  const [gx, gy] = toGraph(transform, x, y);
  const slack = HIT_SLACK_PX / transform.k;
  let best: SimNode | undefined;
  let bestGap = Infinity;
  for (const node of nodes) {
    if (node.x === undefined || node.y === undefined) continue;
    const gap = Math.hypot(node.x - gx, node.y - gy) - node.r;
    if (gap <= slack && gap < bestGap) {
      best = node;
      bestGap = gap;
    }
  }
  return best;
}

/**
 * The node an arrow key moves to: among nodes strictly on that side of the current one, the nearest, where
 * sideways distance counts double so the move follows the row or column rather than jumping diagonally.
 */
export function nearestInDirection(nodes: readonly SimNode[], from: SimNode, direction: Direction): SimNode | undefined {
  const horizontal = direction === "left" || direction === "right";
  const sign = direction === "right" || direction === "down" ? 1 : -1;
  let best: SimNode | undefined;
  let bestScore = Infinity;
  for (const node of nodes) {
    if (node === from || node.x === undefined || node.y === undefined) continue;
    const dx = node.x - (from.x ?? 0);
    const dy = node.y - (from.y ?? 0);
    const along = (horizontal ? dx : dy) * sign;
    if (along <= 0) continue;
    const across = Math.abs(horizontal ? dy : dx);
    const score = along + across * 2;
    if (score < bestScore) {
      best = node;
      bestScore = score;
    }
  }
  return best;
}

/** True when a point of the canvas is in view, with a margin. */
export const inView = (x: number, y: number, size: Size, margin: number): boolean =>
  x >= -margin && y >= -margin && x <= size.width + margin && y <= size.height + margin;

/**
 * Which nodes get a label: the highlighted ones always, the hubs at every zoom, and every node in view once
 * the scale reaches the threshold. A node outside the canvas is never labelled.
 */
export function labelledNodes(scene: Scene, transform: ViewTransform, size: Size, highlighted: ReadonlySet<string> | null): SimNode[] {
  const zoomedIn = transform.k >= LABEL_ZOOM_THRESHOLD;
  return scene.nodes.filter((node) => {
    if (node.x === undefined || node.y === undefined) return false;
    const [sx, sy] = toScreen(transform, node.x, node.y);
    if (!inView(sx, sy, size, MAX_RADIUS)) return false;
    return Boolean(highlighted?.has(node.id)) || scene.hubs.has(node.id) || zoomedIn;
  });
}

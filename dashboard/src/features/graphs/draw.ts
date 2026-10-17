import { LABEL_FONT_PX } from "./config";
import { kindPaint, PAINT_TOKENS } from "./kinds";
import type { Scene, SimNode } from "./layout";
import { labelledNodes, toScreen, type Size, type ViewTransform } from "./view";

/** Custom property name to its resolved colour for the current theme. */
export type Palette = Record<string, string>;

const FALLBACK_COLOUR = "currentColor";
const FALLBACK_FONT = "sans-serif";
const EDGE_ALPHA = 0.55;
const DIMMED_ALPHA = 0.1;
const DIMMED_NODE_ALPHA = 0.3;
const RING_PX = 3;
const HALO_PX = 3;
const LABEL_GAP_PX = 5;

/** Resolves every token the drawing uses against the canvas element, so it follows the theme class. */
export function readPalette(canvas: HTMLCanvasElement): Palette {
  const style = getComputedStyle(canvas);
  return Object.fromEntries(PAINT_TOKENS.map((token) => [token, style.getPropertyValue(token).trim() || FALLBACK_COLOUR]));
}

export function readFontFamily(canvas: HTMLCanvasElement): string {
  return getComputedStyle(canvas).fontFamily || FALLBACK_FONT;
}

export interface Frame {
  size: Size;
  /** Device pixels per CSS pixel. */
  ratio: number;
  transform: ViewTransform;
  palette: Palette;
  fontFamily: string;
  scene: Scene;
  /** The node whose neighbourhood is lit (hovered, else selected); the rest is dimmed. */
  active: SimNode | undefined;
  selected: SimNode | undefined;
  /** The node the keyboard is on, drawn with a ring and a label. */
  focused: SimNode | undefined;
}

const colour = (palette: Palette, token: string): string => palette[token] ?? FALLBACK_COLOUR;

/** Paints one frame: edges, then nodes, then the labels at a constant screen size. */
export function paint(ctx: CanvasRenderingContext2D, frame: Frame): void {
  const { size, ratio, transform, palette, scene, active, selected, focused } = frame;
  const lit = active ? new Set([active.id, ...(scene.neighbours.get(active.id) ?? [])]) : null;

  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, size.width, size.height);
  ctx.save();
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.k, transform.k);

  ctx.lineWidth = 1.5 / transform.k;
  ctx.strokeStyle = colour(palette, "--border-strong");
  for (const dimmedPass of active ? [true, false] : [false]) {
    ctx.globalAlpha = dimmedPass ? DIMMED_ALPHA : active ? 1 : EDGE_ALPHA;
    ctx.beginPath();
    for (const link of scene.links) {
      const touches = !active || link.source === active || link.target === active;
      if (touches === dimmedPass) continue;
      ctx.moveTo(link.source.x ?? 0, link.source.y ?? 0);
      ctx.lineTo(link.target.x ?? 0, link.target.y ?? 0);
    }
    ctx.stroke();
  }

  for (const node of scene.nodes) {
    const dimmed = lit !== null && !lit.has(node.id);
    const looks = kindPaint(node.kind);
    ctx.globalAlpha = dimmed ? DIMMED_NODE_ALPHA : 1;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, node.r, 0, Math.PI * 2);
    ctx.fillStyle = colour(palette, looks.fill);
    ctx.fill();
    ctx.lineWidth = looks.strokeWidth / transform.k;
    ctx.strokeStyle = colour(palette, looks.stroke);
    ctx.stroke();
    if (node === selected || node === focused) {
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.r + RING_PX / transform.k, 0, Math.PI * 2);
      ctx.lineWidth = RING_PX / transform.k;
      ctx.strokeStyle = colour(palette, "--ring");
      ctx.stroke();
    }
  }
  ctx.restore();

  // Labels are drawn in screen space so they keep their size at every zoom.
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.font = `${LABEL_FONT_PX}px ${frame.fontFamily}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.lineJoin = "round";
  ctx.lineWidth = HALO_PX;
  const shown = new Set<string>(lit ?? []);
  if (focused) shown.add(focused.id);
  for (const node of labelledNodes(scene, transform, size, shown)) {
    const [sx, sy] = toScreen(transform, node.x ?? 0, node.y ?? 0);
    const y = sy - node.r * transform.k - LABEL_GAP_PX;
    ctx.globalAlpha = lit !== null && !lit.has(node.id) ? DIMMED_NODE_ALPHA : 1;
    ctx.strokeStyle = colour(palette, "--background");
    ctx.strokeText(node.label, sx, y);
    ctx.fillStyle = colour(palette, "--foreground");
    ctx.fillText(node.label, sx, y);
  }
  ctx.globalAlpha = 1;
}

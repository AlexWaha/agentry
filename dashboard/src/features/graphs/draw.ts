import {
  ACTIVE_RING_PX,
  BASE_LINK_ALPHA,
  BASE_LINK_PX,
  DIM_LINK_ALPHA,
  DIM_NODE_ALPHA,
  LABEL_FONT_PX,
  LABEL_GAP_PX,
  LIT_LINK_PX,
  RING_PX,
} from "./config";
import { isLitLink, litNodes } from "./highlight";
import { kindPaint, PAINT_TOKENS } from "./kinds";
import type { Scene, SimNode } from "./layout";
import { labelledNodes, placeLabels, toScreen, type LabelBox, type Size, type ViewTransform } from "./view";

/** Custom property name to its resolved colour for the current theme. */
export type Palette = Record<string, string>;

const FALLBACK_COLOUR = "currentColor";
const FALLBACK_FONT = "sans-serif";
const HALO_PX = 3;

/** Resolves every token the drawing uses against the canvas element, so it follows the theme class. */
export function readPalette(canvas: HTMLElement): Palette {
  const style = getComputedStyle(canvas);
  return Object.fromEntries(PAINT_TOKENS.map((token) => [token, style.getPropertyValue(token).trim() || FALLBACK_COLOUR]));
}

export function readFontFamily(canvas: HTMLElement): string {
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
  /** The node whose neighbourhood is lit (hovered, else selected); the rest is strongly dimmed. */
  active: SimNode | undefined;
  selected: SimNode | undefined;
  /** The node the keyboard is on, drawn with a ring and a label. */
  focused: SimNode | undefined;
}

const colour = (palette: Palette, token: string): string => palette[token] ?? FALLBACK_COLOUR;

/**
 * Paints one frame (FR-108). Without an active node every link is clearly visible. With one, the node and its
 * neighbours keep full strength and get a ring, the links that touch the node are thick and in the accent colour,
 * and everything else is strongly dimmed. Labels come last, at a constant screen size, and never overlap.
 */
export function paint(ctx: CanvasRenderingContext2D, frame: Frame): void {
  const { size, ratio, transform, palette, scene, active, selected, focused } = frame;
  const lit = litNodes(scene, active?.id);
  const k = transform.k;

  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, size.width, size.height);
  ctx.save();
  ctx.translate(transform.x, transform.y);
  ctx.scale(k, k);
  ctx.lineCap = "round";

  const strokeLinks = (pick: (touches: boolean) => boolean, style: string, alpha: number, widthPx: number): void => {
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = style;
    ctx.lineWidth = widthPx / k;
    ctx.beginPath();
    for (const link of scene.links) {
      if (!pick(isLitLink(active?.id, link.source.id, link.target.id))) continue;
      ctx.moveTo(link.source.x ?? 0, link.source.y ?? 0);
      ctx.lineTo(link.target.x ?? 0, link.target.y ?? 0);
    }
    ctx.stroke();
  };
  if (active) {
    strokeLinks((touches) => !touches, colour(palette, "--muted-foreground"), DIM_LINK_ALPHA, BASE_LINK_PX);
    strokeLinks((touches) => touches, colour(palette, "--ring"), 1, LIT_LINK_PX);
  } else {
    strokeLinks(() => true, colour(palette, "--muted-foreground"), BASE_LINK_ALPHA, BASE_LINK_PX);
  }

  for (const node of scene.nodes) {
    const dimmed = lit !== null && !lit.has(node.id);
    const looks = kindPaint(node.kind);
    ctx.globalAlpha = dimmed ? DIM_NODE_ALPHA : 1;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, node.r, 0, Math.PI * 2);
    ctx.fillStyle = colour(palette, looks.fill);
    ctx.fill();
    ctx.lineWidth = looks.strokeWidth / k;
    ctx.strokeStyle = colour(palette, looks.stroke);
    ctx.stroke();
    const strong = node === active || node === selected || node === focused;
    if (strong || (lit !== null && lit.has(node.id))) {
      const ring = (strong ? ACTIVE_RING_PX : RING_PX) / k;
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.r + ring, 0, Math.PI * 2);
      ctx.lineWidth = ring;
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
  const forced = new Set<string>();
  for (const node of [active, selected, focused]) if (node) forced.add(node.id);
  const boxes: LabelBox[] = labelledNodes(scene, transform, size, lit, forced).map((node) => {
    const [sx, sy] = toScreen(transform, node.x ?? 0, node.y ?? 0);
    return {
      id: node.id,
      x: sx,
      y: sy - node.r * k - LABEL_GAP_PX - 1,
      width: ctx.measureText(node.label).width,
      height: LABEL_FONT_PX + 2,
      forced: forced.has(node.id),
    };
  });
  ctx.globalAlpha = 1;
  for (const box of placeLabels(boxes)) {
    const label = scene.byId.get(box.id)?.label ?? "";
    ctx.strokeStyle = colour(palette, "--background");
    ctx.strokeText(label, box.x, box.y);
    ctx.fillStyle = colour(palette, "--foreground");
    ctx.fillText(label, box.x, box.y);
  }
}

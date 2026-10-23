import type { Scene } from "./layout";

/*
 * The highlight rules both views share (FR-108): the node under the pointer, else the selected one, is the
 * active node; it and its neighbours stay at full strength, every other node and link is strongly dimmed.
 */

/** The ids lit by an active node: the node and its neighbours. Null when nothing is active, so nothing dims. */
export function litNodes(scene: Scene, activeId: string | null | undefined): Set<string> | null {
  if (!activeId || !scene.byId.has(activeId)) return null;
  return new Set([activeId, ...(scene.neighbours.get(activeId) ?? [])]);
}

/** True when a link touches the active node: these links are drawn thick and in the accent colour. */
export const isLitLink = (activeId: string | null | undefined, sourceId: string, targetId: string): boolean =>
  Boolean(activeId) && (sourceId === activeId || targetId === activeId);

export type Rgb = readonly [number, number, number];

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const FUNCTIONAL = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i;
/** Used when a colour cannot be read (an unresolved token, `currentColor`): a mid grey that shows on both themes. */
const UNREADABLE: Rgb = [128, 128, 128];

/** Reads `#rgb`, `#rrggbb`, `rgb()` and `rgba()`, which is what the theme tokens resolve to. */
export function parseColour(css: string): Rgb {
  const text = css.trim();
  const hex = HEX.exec(text);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
    return [parseInt(digits.slice(0, 2), 16), parseInt(digits.slice(2, 4), 16), parseInt(digits.slice(4, 6), 16)];
  }
  const functional = FUNCTIONAL.exec(text);
  if (functional) return [Number(functional[1]), Number(functional[2]), Number(functional[3])];
  return UNREADABLE;
}

/** `rgb()` of `from` moved `amount` (0 to 1) of the way to `to`: how a dimmed node blends into the background. */
export function mixColours(from: string, to: string, amount: number): string {
  const [fr, fg, fb] = parseColour(from);
  const [tr, tg, tb] = parseColour(to);
  const mix = (a: number, b: number): number => Math.round(a + (b - a) * amount);
  return `rgb(${mix(fr, tr)}, ${mix(fg, tg)}, ${mix(fb, tb)})`;
}

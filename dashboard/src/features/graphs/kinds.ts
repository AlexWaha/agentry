import { t } from "@/i18n";

/*
 * Node colour by kind, from semantic roles only (no raw colour). Two nodes of different kinds also differ by
 * fill versus ring, and every node carries its kind in its accessible name and in the table view, so colour
 * is never the only signal (FR-91). Class names are written out in full so Tailwind finds them.
 */
const SOLID = "stroke-card stroke-1";
const KIND_CLASS: Record<string, string> = {
  lesson: `fill-status-active-fg ${SOLID}`,
  pattern: `fill-status-done-fg ${SOLID}`,
  task: `fill-muted-foreground ${SOLID}`,
  agent: `fill-foreground ${SOLID}`,
  area: "fill-card stroke-foreground stroke-2",
  module: `fill-status-active-fg ${SOLID}`,
  file: `fill-muted-foreground ${SOLID}`,
};
const FALLBACK_CLASS = `fill-muted-foreground ${SOLID}`;

export const kindClass = (kind: string): string => KIND_CLASS[kind] ?? FALLBACK_CLASS;

/** The display name of a node kind; an unknown kind shows as written. */
export function kindLabel(kind: string): string {
  const key = `graphs.kind.${kind}`;
  const label = t(key);
  return label === key ? kind : label;
}

/*
 * The same colours for the canvas drawing, which cannot use Tailwind classes: each kind names the semantic CSS
 * custom properties it paints with (resolved against the live theme when the canvas is drawn), so the legend
 * and the canvas agree and the theme switch repaints both.
 */
export interface KindPaint {
  fill: string;
  stroke: string;
  strokeWidth: number;
}

const SOLID_PAINT = { stroke: "--card", strokeWidth: 1 };
const KIND_PAINT: Record<string, KindPaint> = {
  lesson: { fill: "--status-active-fg", ...SOLID_PAINT },
  pattern: { fill: "--status-done-fg", ...SOLID_PAINT },
  task: { fill: "--muted-foreground", ...SOLID_PAINT },
  agent: { fill: "--foreground", ...SOLID_PAINT },
  area: { fill: "--card", stroke: "--foreground", strokeWidth: 2 },
  module: { fill: "--status-active-fg", ...SOLID_PAINT },
  file: { fill: "--muted-foreground", ...SOLID_PAINT },
};
const FALLBACK_PAINT: KindPaint = { fill: "--muted-foreground", ...SOLID_PAINT };

export const kindPaint = (kind: string): KindPaint => KIND_PAINT[kind] ?? FALLBACK_PAINT;

/** Every custom property the canvas reads, so the palette can be resolved in one pass. */
export const PAINT_TOKENS: readonly string[] = [
  ...new Set([...Object.values(KIND_PAINT), FALLBACK_PAINT].flatMap((paint) => [paint.fill, paint.stroke])),
  "--ring",
  "--foreground",
  "--background",
  "--muted",
];

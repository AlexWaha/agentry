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

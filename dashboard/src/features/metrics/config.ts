/** The ranges of the Metrics range control, in days (FR-101). */
export const METRICS_RANGES = [7, 14, 30, 90] as const;
export type MetricsRange = (typeof METRICS_RANGES)[number];

/** The range shown when the URL names none; it is left out of the URL. */
export const DEFAULT_RANGE: MetricsRange = 14;

/** Bar colours by series position; semantic tokens only, so both themes work. */
export const SERIES_FILLS = ["fill-status-active-edge", "fill-foreground", "fill-status-done-edge", "fill-muted-foreground"] as const;

/** Layout of the horizontal bar chart, in SVG units (the svg scales to its container). */
export const HORIZONTAL = {
  width: 480,
  labelWidth: 124,
  valueWidth: 64,
  barHeight: 9,
  barGap: 2,
  groupGap: 10,
  labelMaxChars: 20,
} as const;

/** Layout of the vertical bar chart, in SVG units. */
export const VERTICAL = {
  width: 560,
  height: 180,
  axisWidth: 34,
  top: 14,
  bottom: 20,
  /** Share of a day's slot left empty between two days. */
  slotGap: 0.25,
  /** Most day labels along the x axis; a longer range prints every n-th label. */
  maxLabels: 10,
} as const;

export const BAR_RADIUS = 1.5;

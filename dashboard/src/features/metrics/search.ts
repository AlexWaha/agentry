import { textParam } from "@/lib/search-params";

import { DEFAULT_RANGE, METRICS_RANGES, type MetricsRange } from "./config";

export interface MetricsSearch {
  days?: MetricsRange;
}

/** The range of the URL: one of the offered ranges other than the default, anything else is dropped. */
export function validateMetricsSearch(raw: Record<string, unknown>): MetricsSearch {
  const days = METRICS_RANGES.find((range) => String(range) === textParam(raw.days));
  return { days: days === DEFAULT_RANGE ? undefined : days };
}

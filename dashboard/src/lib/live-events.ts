import type { EventsParams } from "@/config/query-keys";
import type { Event, EventsMeta } from "@/types/api";

export interface CachedEvents {
  data: Event[];
  meta: EventsMeta;
}

/** Whether a journal row belongs in the list the filters describe: the same rules the API applies (FR-25). */
export function matchesFilters(row: Event, filters: EventsParams): boolean {
  if (filters.task && !row.task.toLowerCase().includes(filters.task.toLowerCase())) return false;
  if (filters.kind && row.kind !== filters.kind) return false;
  if (filters.result && row.outcome !== filters.result) return false;
  return true;
}

/**
 * Applies rows from `events.appended` (ascending id) to one cached list without a request (FR-100). A row that
 * the cached answer already covers (id at or below `meta.latest_id`, the race between a fetch and the socket, or a
 * row delivered twice) or that the filters exclude changes nothing. Any other row adds one to `meta.total` and
 * advances `meta.latest_id`; on page 1 it is also prepended and the list is trimmed to the page size.
 */
export function applyAppended(cached: CachedEvents, filters: EventsParams, rows: Event[]): CachedEvents {
  const firstPage = (filters.offset ?? 0) === 0;
  let { data, meta } = cached;
  for (const row of rows) {
    if (row.id <= meta.latest_id || !matchesFilters(row, filters)) continue;
    meta = { ...meta, total: meta.total + 1, latest_id: row.id };
    if (firstPage) data = [row, ...data].slice(0, meta.limit);
  }
  return meta === cached.meta ? cached : { data, meta };
}

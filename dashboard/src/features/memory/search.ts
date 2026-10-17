import { enumParam, pageParam, textParam, type PageSearch } from "@/lib/search-params";

/** The `/memory` search params (spec SPA URL contract). `lesson` is the default tab and is omitted from the URL. */
export const MEMORY_TABS = ["lesson", "pattern", "module", "stats"] as const;
export type MemoryTab = (typeof MEMORY_TABS)[number];

export const RETIRED_MODES = ["include", "only"] as const;

export interface MemorySearch extends PageSearch {
  tab?: MemoryTab;
  q?: string;
  agent?: string;
  area?: string;
  task?: string;
  retired?: (typeof RETIRED_MODES)[number];
}

export type FilterKey = "q" | "agent" | "area" | "task" | "retired";

/** The filters each tab can send; switching tab drops the others (FR-103). The Stats tab has none. */
export const TAB_FILTERS: Record<MemoryTab, readonly FilterKey[]> = {
  lesson: ["q", "agent", "area", "task", "retired"],
  pattern: ["q", "task"],
  module: ["q"],
  stats: [],
};

const ALL_FILTERS: readonly FilterKey[] = TAB_FILTERS.lesson;

/**
 * Route validation: invalid values are dropped, and so is any filter that does not apply to the tab. Every key
 * is returned, undefined when dropped: the router merges the result over the raw params, and only an explicit
 * undefined overrides a raw value such as `?tab=bogus`.
 */
export function validateMemorySearch(raw: Record<string, unknown>): MemorySearch {
  const tab = enumParam(raw.tab, MEMORY_TABS) ?? "lesson";
  const allowed = TAB_FILTERS[tab];
  const keep = <T,>(key: FilterKey, value: T | undefined): T | undefined => (allowed.includes(key) ? value : undefined);
  return {
    tab: tab === "lesson" ? undefined : tab,
    q: keep("q", textParam(raw.q)),
    agent: keep("agent", textParam(raw.agent)),
    area: keep("area", textParam(raw.area)),
    task: keep("task", textParam(raw.task)),
    retired: keep("retired", enumParam(raw.retired, RETIRED_MODES)),
    page: tab === "stats" ? undefined : pageParam(raw.page),
  };
}

/** The search patch of a tab switch: the new tab plus every filter that does not apply to it cleared. */
export function tabPatch(tab: MemoryTab): Partial<MemorySearch> {
  const allowed = TAB_FILTERS[tab];
  const patch: Partial<MemorySearch> = { tab: tab === "lesson" ? undefined : tab };
  for (const key of ALL_FILTERS) if (!allowed.includes(key)) patch[key] = undefined;
  return patch;
}

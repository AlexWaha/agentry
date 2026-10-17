import { EVENT_KIND_KEYS } from "@/config/stages";
import { enumParam, pageParam, textParam, type PageSearch } from "@/lib/search-params";

export const RESULT_VALUES = ["pass", "fail"] as const;
export type ResultFilter = (typeof RESULT_VALUES)[number];

/** The task filter the API accepts: letters, digits and hyphens, 1 to 32 characters. */
const TASK_FILTER = /^[A-Za-z0-9-]{1,32}$/;

/** The filters of the Activity page; they and the page number are the search params of its URL (FR-100, FR-124). */
export interface ActivitySearch extends PageSearch {
  task?: string;
  kind?: string;
  result?: ResultFilter;
}

export const ACTIVITY_FILTER_KEYS = ["task", "kind", "result"] as const;

/** Keeps the valid params and drops the rest, so a reload or a shared link never sends the API a value it would refuse. */
export function validateActivitySearch(raw: Record<string, unknown>): ActivitySearch {
  const task = textParam(raw.task)?.trim();
  return {
    task: task && TASK_FILTER.test(task) ? task : undefined,
    kind: enumParam(raw.kind, Object.keys(EVENT_KIND_KEYS)),
    result: enumParam(raw.result, RESULT_VALUES),
    page: pageParam(raw.page),
  };
}

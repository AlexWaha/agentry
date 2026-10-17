import type { Folder } from "@/types/api";
import { enumParam, flagParam, pageParam, textParam } from "@/lib/search-params";

/** The type filter the API accepts: letters, digits, underscores and hyphens, 1 to 32 characters. */
const TYPE_FILTER = /^[A-Za-z0-9_-]{1,32}$/;

export const FOLDERS: readonly Folder[] = ["backlog", "active", "done"];

/** The Tasks page's URL state (FR-124, FR-126): every filter, the superseded toggle and the page. */
export interface TasksSearch {
  folder?: Folder;
  type?: string;
  epic?: string;
  q?: string;
  superseded?: 1;
  page?: number;
}

/** The filters that "Clear filters" resets; the superseded toggle has its own control. */
export const FILTER_KEYS = ["folder", "type", "epic", "q"] as const;

export function validateTasksSearch(raw: Record<string, unknown>): TasksSearch {
  const type = textParam(raw.type);
  return {
    folder: enumParam(raw.folder, FOLDERS),
    type: type && TYPE_FILTER.test(type) ? type : undefined,
    epic: textParam(raw.epic),
    q: textParam(raw.q),
    superseded: flagParam(raw.superseded) ? 1 : undefined,
    page: pageParam(raw.page),
  };
}

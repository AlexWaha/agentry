import { flagParam, textParam } from "@/lib/search-params";

/** The Board's URL state: the epic filter and the superseded toggle (`1` or absent). */
export interface BoardSearch {
  epic?: string;
  superseded?: 1;
}

export function validateBoardSearch(raw: Record<string, unknown>): BoardSearch {
  return { epic: textParam(raw.epic), superseded: flagParam(raw.superseded) ? 1 : undefined };
}

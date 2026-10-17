import { enumParam, textParam, type PageSearch } from "@/lib/search-params";

/** The `/graphs` search params: the view (`memory` is the default and is omitted) and the chosen code index. */
export const GRAPH_VIEWS = ["memory", "code"] as const;
export type GraphView = (typeof GRAPH_VIEWS)[number];

export interface GraphsSearch extends PageSearch {
  view?: GraphView;
  index?: string;
}

/** Every key is returned, undefined when dropped, so the router's merge overrides a raw invalid value. */
export function validateGraphsSearch(raw: Record<string, unknown>): GraphsSearch {
  const view = enumParam(raw.view, GRAPH_VIEWS);
  return {
    view: view === "code" ? view : undefined,
    index: view === "code" ? textParam(raw.index) : undefined,
  };
}

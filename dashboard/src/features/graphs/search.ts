import { enumParam, textParam, type PageSearch } from "@/lib/search-params";

/** The `/graphs` search params: the view (`memory` is the default and is omitted) and the chosen code index. */
export const GRAPH_VIEWS = ["memory", "code"] as const;
export type GraphView = (typeof GRAPH_VIEWS)[number];

/** The drawing mode. Absent means the default: 3D, or 2D when the OS asks for reduced motion. */
export const GRAPH_MODES = ["2d", "3d"] as const;
export type GraphMode = (typeof GRAPH_MODES)[number];

export interface GraphsSearch extends PageSearch {
  view?: GraphView;
  index?: string;
  mode?: GraphMode;
}

/** Every key is returned, undefined when dropped, so the router's merge overrides a raw invalid value. */
export function validateGraphsSearch(raw: Record<string, unknown>): GraphsSearch {
  const view = enumParam(raw.view, GRAPH_VIEWS);
  return {
    view: view === "code" ? view : undefined,
    index: view === "code" ? textParam(raw.index) : undefined,
    mode: enumParam(raw.mode, GRAPH_MODES),
  };
}

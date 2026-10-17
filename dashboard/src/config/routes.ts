/** Route patterns by page id. The page ids are also the keys of docs-links.json (FR-78, FR-112). */
export const ROUTES = {
  overview: "/",
  needs: "/needs",
  board: "/board",
  tasks: "/tasks",
  task: "/tasks/$taskId",
  activity: "/activity",
  metrics: "/metrics",
  memory: "/memory",
  graphs: "/graphs",
  harness: "/harness",
  handoffs: "/handoffs",
  handoff: "/handoffs/$taskId",
} as const;

export type PageId = keyof typeof ROUTES;

export const PAGE_IDS = Object.keys(ROUTES) as PageId[];

/** Concrete paths for plain hrefs and navigation; the id is percent-encoded once. */
export const taskPath = (id: string): string => `/tasks/${encodeURIComponent(id)}`;
export const handoffPath = (id: string): string => `/handoffs/${encodeURIComponent(id)}`;

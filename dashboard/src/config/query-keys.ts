import type { Folder, MemoryKind } from "@/types/api";

/**
 * Query-string value kinds the API accepts; an absent, null or empty value is left out. The params below are
 * type aliases, not interfaces, so they are assignable to QueryParams (an interface has no index signature).
 */
export type ParamValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, ParamValue>;

export type RunsParams = {
  lane?: string;
};

export type TasksParams = {
  folder?: Folder;
  epic?: string;
  type?: string;
  q?: string;
  include_superseded?: boolean;
  limit?: number;
  offset?: number;
};

export type EventsParams = {
  task?: string;
  kind?: string;
  result?: "pass" | "fail";
  limit?: number;
  offset?: number;
};

export type MetricsParams = {
  since?: string;
  days?: number;
};

export type MemoryParams = {
  kind?: MemoryKind;
  q?: string;
  agent?: string;
  area?: string;
  task?: string;
  retired?: "exclude" | "include" | "only";
  limit?: number;
  offset?: number;
};

export type HandoffsParams = {
  limit?: number;
  offset?: number;
};

export type GraphParams = {
  level?: "module" | "file";
  limit?: number;
};

/**
 * The params of one request without the empty ones, keys sorted. The same cleaned object builds the
 * query string and the query key, so two different pages of one list can never share a key and two
 * spellings of the same request always do (FR-81).
 */
export function cleanParams<T extends QueryParams>(params?: T): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  if (!params) return out;
  for (const key of Object.keys(params).sort()) {
    const value = params[key];
    if (value === undefined || value === null || value === "") continue;
    out[key] = value;
  }
  return out;
}

/**
 * One key per resource and per distinct parameter set. The FIRST element is the resource name that
 * TOPIC_RESOURCES (topics.ts) maps an `invalidate` topic to.
 */
export const queryKeys = {
  status: () => ["status"] as const,
  runs: (params?: RunsParams) => ["runs", cleanParams(params)] as const,
  tasks: (params?: TasksParams) => ["tasks", cleanParams(params)] as const,
  task: (id: string) => ["task", id] as const,
  epics: () => ["epics"] as const,
  events: (params?: EventsParams) => ["events", cleanParams(params)] as const,
  metrics: (params?: MetricsParams) => ["metrics", cleanParams(params)] as const,
  memory: (params?: MemoryParams) => ["memory", cleanParams(params)] as const,
  memoryStats: () => ["memoryStats"] as const,
  memoryGraph: () => ["memoryGraph"] as const,
  codegraph: () => ["codegraph"] as const,
  codegraphGraph: (key: string, params?: GraphParams) => ["codegraphGraph", key, cleanParams(params)] as const,
  harness: () => ["harness"] as const,
  handoffs: (params?: HandoffsParams) => ["handoffs", cleanParams(params)] as const,
  handoff: (id: string) => ["handoff", id] as const,
};

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import {
  queryKeys,
  type EventsParams,
  type GraphParams,
  type HandoffsParams,
  type MemoryParams,
  type MetricsParams,
  type RunsParams,
  type TasksParams,
} from "@/config/query-keys";
import { apiGet, type ApiResult } from "@/lib/api-client";
import type {
  CodeIndex,
  Epic,
  Event,
  EventsMeta,
  Graph,
  GraphMeta,
  Handoff,
  HandoffDoc,
  Harness,
  MemoryRow,
  MemoryStats,
  Metrics,
  PageMeta,
  Run,
  Status,
  TaskDetail,
  TaskSummary,
  TasksMeta,
} from "@/types/api";

/*
 * One query-options factory per route. Routes that carry paging or graph metadata resolve to
 * `{data, meta}` (ApiResult); every other route resolves to its `data` alone. Paged lists keep the previous
 * page on screen while the next one loads. Page tasks use these instead of building keys by hand, so the
 * key of a request always equals queryKeys.* for the same parameters (FR-81).
 */

const unwrap = async <T>(request: Promise<ApiResult<T>>): Promise<T> => (await request).data;

export const statusQuery = () =>
  queryOptions({
    queryKey: queryKeys.status(),
    queryFn: ({ signal }) => unwrap(apiGet<Status>("/status", undefined, { signal })),
  });

export const runsQuery = (params?: RunsParams) =>
  queryOptions({
    queryKey: queryKeys.runs(params),
    queryFn: ({ signal }) => unwrap(apiGet<Run[]>("/runs", { ...params }, { signal })),
  });

export const tasksQuery = (params?: TasksParams) =>
  queryOptions({
    queryKey: queryKeys.tasks(params),
    queryFn: ({ signal }) => apiGet<TaskSummary[], TasksMeta>("/tasks", { ...params }, { signal }),
    placeholderData: keepPreviousData,
  });

export const taskQuery = (id: string) =>
  queryOptions({
    queryKey: queryKeys.task(id),
    queryFn: ({ signal }) => unwrap(apiGet<TaskDetail>(`/tasks/${encodeURIComponent(id)}`, undefined, { signal })),
  });

export const epicsQuery = () =>
  queryOptions({
    queryKey: queryKeys.epics(),
    queryFn: ({ signal }) => unwrap(apiGet<Epic[]>("/epics", undefined, { signal })),
  });

export const eventsQuery = (params?: EventsParams) =>
  queryOptions({
    queryKey: queryKeys.events(params),
    queryFn: ({ signal }) => apiGet<Event[], EventsMeta>("/events", { ...params }, { signal }),
    placeholderData: keepPreviousData,
  });

export const metricsQuery = (params?: MetricsParams) =>
  queryOptions({
    queryKey: queryKeys.metrics(params),
    queryFn: ({ signal }) => unwrap(apiGet<Metrics>("/metrics", { ...params }, { signal })),
  });

export const memoryQuery = (params?: MemoryParams) =>
  queryOptions({
    queryKey: queryKeys.memory(params),
    queryFn: ({ signal }) => apiGet<MemoryRow[], PageMeta>("/memory", { ...params }, { signal }),
    placeholderData: keepPreviousData,
  });

export const memoryStatsQuery = () =>
  queryOptions({
    queryKey: queryKeys.memoryStats(),
    queryFn: ({ signal }) => unwrap(apiGet<MemoryStats>("/memory/stats", undefined, { signal })),
  });

export const memoryGraphQuery = () =>
  queryOptions({
    queryKey: queryKeys.memoryGraph(),
    queryFn: ({ signal }) => unwrap(apiGet<Graph>("/graphs/memory", undefined, { signal })),
  });

export const codegraphQuery = () =>
  queryOptions({
    queryKey: queryKeys.codegraph(),
    queryFn: ({ signal }) => unwrap(apiGet<CodeIndex[]>("/codegraph", undefined, { signal })),
  });

export const codegraphGraphQuery = (key: string, params?: GraphParams) =>
  queryOptions({
    queryKey: queryKeys.codegraphGraph(key, params),
    queryFn: ({ signal }) =>
      apiGet<Graph, GraphMeta>(`/codegraph/${encodeURIComponent(key)}/graph`, { ...params }, { signal }),
  });

export const harnessQuery = () =>
  queryOptions({
    queryKey: queryKeys.harness(),
    queryFn: ({ signal }) => unwrap(apiGet<Harness>("/harness", undefined, { signal })),
  });

export const handoffsQuery = (params?: HandoffsParams) =>
  queryOptions({
    queryKey: queryKeys.handoffs(params),
    queryFn: ({ signal }) => apiGet<Handoff[], PageMeta>("/handoffs", { ...params }, { signal }),
    placeholderData: keepPreviousData,
  });

export const handoffQuery = (id: string) =>
  queryOptions({
    queryKey: queryKeys.handoff(id),
    queryFn: ({ signal }) => unwrap(apiGet<HandoffDoc>(`/handoffs/${encodeURIComponent(id)}`, undefined, { signal })),
  });

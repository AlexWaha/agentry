import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, type RenderResult } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";

import { createAppRouter, featureModules } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { startWsClient } from "@/lib/ws-client";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import epicsFixture from "@/test/fixtures/pipeline/epics.json";
import eventsFixture from "@/test/fixtures/pipeline/events.json";
import taskDetailFixture from "@/test/fixtures/pipeline/task-detail.json";
import tasksFixture from "@/test/fixtures/pipeline/tasks-list.json";
import handoffFixture from "@/test/fixtures/harness/handoff.json";
import handoffsFixture from "@/test/fixtures/harness/handoffs.json";
import harnessFixture from "@/test/fixtures/harness/harness.json";
import statusFixture from "@/test/fixtures/shell/status.json";
import runsFixture from "@/test/fixtures/shell/runs.json";
import metricsFixture from "@/test/fixtures/activity/metrics.json";
import lessonsFixture from "@/test/fixtures/memory/lessons.json";
import memoryStatsFixture from "@/test/fixtures/memory/stats.json";
import memoryGraphFixture from "@/test/fixtures/memory/graph-memory.json";
import codegraphFixture from "@/test/fixtures/memory/codegraph.json";
import { fail, mockApi, ok, type ApiMock, type MockHandler } from "@/test/utils/api-mock";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { installFakeWebSocket } from "@/test/utils/fake-ws";
import { codeGraph, codeGraphMeta } from "@/test/fixtures/memory/codegraph-graph";

/*
 * QA harness: the real app (shell, every feature route, the production QueryClient) on a memory history, with a
 * fake API built from the shipped fixtures. Tests override single routes through `routes`.
 */

export const DEFAULT_ROUTES: Record<string, MockHandler> = {
  "/api/v1/status": fromFixture(statusFixture),
  "/api/v1/runs": fromFixture(runsFixture),
  "/api/v1/tasks": fromFixture(tasksFixture),
  "/api/v1/tasks/task-0104": fromFixture(taskDetailFixture),
  "/api/v1/epics": fromFixture(epicsFixture),
  "/api/v1/events": fromFixture(eventsFixture),
  "/api/v1/metrics": fromFixture(metricsFixture),
  "/api/v1/memory": fromFixture(lessonsFixture),
  "/api/v1/memory/stats": fromFixture(memoryStatsFixture),
  "/api/v1/graphs/memory": fromFixture(memoryGraphFixture),
  "/api/v1/codegraph": fromFixture(codegraphFixture),
  "/api/v1/codegraph/root/graph": ok(codeGraph, codeGraphMeta),
  "/api/v1/harness": fromFixture(harnessFixture),
  "/api/v1/handoffs": fromFixture(handoffsFixture),
  "/api/v1/handoffs/task-0107": fromFixture(handoffFixture),
};

export interface QaApp extends RenderResult {
  api: ApiMock;
  queryClient: QueryClient;
  router: ReturnType<typeof createAppRouter>;
  stopWs: () => void;
}

export interface QaAppOptions {
  routes?: Record<string, MockHandler>;
  /** Start the WebSocket client against a FakeWebSocket (default false). */
  ws?: boolean;
}

export async function renderApp(url: string, options: QaAppOptions = {}): Promise<QaApp> {
  installVirtualClock();
  const api = mockApi({ ...DEFAULT_ROUTES, ...options.routes });
  const queryClient = createQueryClient();
  let stopWs = (): void => undefined;
  if (options.ws) {
    installFakeWebSocket();
    stopWs = startWsClient(queryClient);
  }
  const router = createAppRouter(featureModules, createMemoryHistory({ initialEntries: [url] }));
  await router.load();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, api, queryClient, router, stopWs };
}

/*
 * Time in the QA tests is virtual: renderApp installs fake timers (timeouts and intervals only, so performance.now
 * and the microtask queue stay real) and settle() advances them. A debounce of 300 ms costs no wall-clock time and
 * cannot flake; nothing in src/test/qa sleeps.
 */
const FAKED_TIMERS = ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] as const;

afterEach(() => {
  vi.useRealTimers();
});

// Testing Library only advances fake timers when it finds a Jest-shaped global; give it one that drives vitest's clock.
Object.assign(globalThis, { jest: { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) } });

/** One turn of the real event loop (setImmediate is not faked): lets module loads of lazy routes finish. Not a wait. */
const ioTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

/** Advances the virtual clock by `ms` inside act, flushing promises and pending module loads (debounces, router loads, query notifications). */
export const settle = (ms = 50): Promise<void> =>
  act(async () => {
    await ioTurn();
    await vi.advanceTimersByTimeAsync(ms);
    await ioTurn();
    await vi.advanceTimersByTimeAsync(0);
  });

function installVirtualClock(): void {
  if (!vi.isFakeTimers()) vi.useFakeTimers({ toFake: [...FAKED_TIMERS] });
}

/**
 * Re-runs `assertion` until it holds, one real event-loop turn plus 20 ms of virtual time per attempt, for pages that
 * wait on a lazy module whose load needs real I/O. Throws the last assertion error after `attempts` tries.
 */
export async function until(assertion: () => void, attempts = 500): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      if (attempt >= attempts) throw error;
      await act(async () => {
        await ioTurn();
        await vi.advanceTimersByTimeAsync(20);
      });
    }
  }
}

/** A user-event instance whose delays run on the virtual clock. */
export const qaUser = () => {
  installVirtualClock();
  return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTimeAsync(ms) });
};

/**
 * axe-core schedules its own work with real timers, so it runs on the real clock; the virtual one resumes afterwards.
 * The DOM is not touched by any timer in between, so the result is the same as on the virtual clock.
 */
export async function axeClean(element: Element): Promise<void> {
  vi.useRealTimers();
  try {
    await expectNoAxeViolations(element);
  } finally {
    installVirtualClock();
  }
}

const OFFSET_PATTERN = /^\d+$/;

/**
 * A paged route as the real server answers it (FR-123): `data` holds at most `limit` rows from `offset`, `meta.total`
 * is the filtered count, and an `offset` or `limit` that is not a plain integer is a 422 with the field named.
 */
export function pagedHandler<T>(
  all: T[],
  options: { filter?: (row: T, url: URL) => boolean; meta?: (url: URL, matching: T[]) => object; defaultLimit?: number } = {},
): MockHandler {
  return (url) => {
    const rawLimit = url.searchParams.get("limit") ?? String(options.defaultLimit ?? 25);
    const rawOffset = url.searchParams.get("offset") ?? "0";
    const errors: Record<string, string[]> = {};
    if (!OFFSET_PATTERN.test(rawLimit)) errors.limit = ["must be an integer"];
    if (!OFFSET_PATTERN.test(rawOffset)) errors.offset = ["must be an integer"];
    if (Object.keys(errors).length > 0) return fail(422, "validation_failed", "Invalid query parameters", errors);
    const limit = Number(rawLimit);
    const offset = Number(rawOffset);
    const matching = options.filter ? all.filter((row) => options.filter?.(row, url)) : all;
    return ok(matching.slice(offset, offset + limit), { total: matching.length, limit, offset, ...(options.meta?.(url, matching) ?? {}) });
  };
}

export { ok, fail };

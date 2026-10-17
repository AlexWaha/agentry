import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryKeys } from "@/config/query-keys";
import { getSocketState, onEventsAppended, parseWsMessage, startWsClient } from "@/lib/ws-client";
import { FakeWebSocket, helloMessage, installFakeWebSocket, invalidateMessage } from "@/test/utils/fake-ws";
import type { Event } from "@/types/api";

let client: QueryClient;
let stop: (() => void) | undefined;

beforeEach(() => {
  vi.useFakeTimers();
  installFakeWebSocket();
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  localStorage.setItem("agentry-dashboard-token", "tok-1");
});

afterEach(() => {
  stop?.();
  stop = undefined;
  vi.useRealTimers();
});

/** Mounts an observer so an invalidation of the key shows up as a fetch. */
async function observe(key: readonly unknown[]): Promise<{ fetches: () => number; stop: () => void }> {
  let count = 0;
  const observer = new QueryObserver(client, {
    queryKey: key,
    queryFn: () => {
      count += 1;
      return count;
    },
  });
  const unsubscribe = observer.subscribe(() => undefined);
  await vi.advanceTimersByTimeAsync(0);
  return { fetches: () => count, stop: unsubscribe };
}

describe("connection state", () => {
  it("reads reconnecting before hello and live after it", () => {
    stop = startWsClient(client);
    expect(getSocketState()).toBe("reconnecting");

    FakeWebSocket.last.open();
    expect(getSocketState()).toBe("reconnecting");

    FakeWebSocket.last.receive(helloMessage);
    expect(getSocketState()).toBe("live");
  });

  it("connects to /ws with the stored token and never sends a message", () => {
    stop = startWsClient(client);
    FakeWebSocket.last.open();
    FakeWebSocket.last.receive(helloMessage);
    FakeWebSocket.last.receive(invalidateMessage("runs"));
    vi.advanceTimersByTime(1000);

    expect(FakeWebSocket.last.url).toMatch(/^ws:\/\/[^/]+\/ws\?token=tok-1$/);
    expect(FakeWebSocket.last.sent).toEqual([]);
  });

  it("goes back to reconnecting when the connection drops", () => {
    stop = startWsClient(client);
    FakeWebSocket.last.receive(helloMessage);
    expect(getSocketState()).toBe("live");

    FakeWebSocket.last.serverClose();

    expect(getSocketState()).toBe("reconnecting");
  });
});

describe("reconnect", () => {
  it("waits 1, 2, 4, 8, 16, 30 and 30 seconds between failed attempts", () => {
    stop = startWsClient(client);
    const waits: number[] = [];

    for (let attempt = 0; attempt < 7; attempt += 1) {
      const before = FakeWebSocket.instances.length;
      FakeWebSocket.last.serverClose();
      let waited = 0;
      while (FakeWebSocket.instances.length === before) {
        vi.advanceTimersByTime(1000);
        waited += 1000;
      }
      waits.push(waited / 1000);
    }

    expect(waits).toEqual([1, 2, 4, 8, 16, 30, 30]);
  });

  it("starts the delays over after a successful hello", () => {
    stop = startWsClient(client);
    FakeWebSocket.last.serverClose();
    vi.advanceTimersByTime(1000);
    FakeWebSocket.last.serverClose();
    vi.advanceTimersByTime(2000);
    FakeWebSocket.last.receive(helloMessage);
    const before = FakeWebSocket.instances.length;

    FakeWebSocket.last.serverClose();
    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances).toHaveLength(before);
    vi.advanceTimersByTime(1);

    expect(FakeWebSocket.instances).toHaveLength(before + 1);
  });

  it("stops reconnecting after the stop function is called", () => {
    stop = startWsClient(client);
    FakeWebSocket.last.serverClose();
    stop();
    stop = undefined;

    vi.advanceTimersByTime(60_000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe("invalidation", () => {
  it("invalidates every query on connect and again on resync", async () => {
    const status = await observe(queryKeys.status());
    const tasks = await observe(queryKeys.tasks());
    stop = startWsClient(client);

    FakeWebSocket.last.receive(helloMessage);
    await vi.advanceTimersByTimeAsync(0);
    expect([status.fetches(), tasks.fetches()]).toEqual([2, 2]);

    FakeWebSocket.last.receive({ type: "resync", v: 1, payload: { reason: "overflow" } });
    await vi.advanceTimersByTimeAsync(0);
    expect([status.fetches(), tasks.fetches()]).toEqual([3, 3]);
  });

  it("coalesces three invalidations of runs within 400 ms into one refetch", async () => {
    const runs = await observe(queryKeys.runs());
    const memory = await observe(queryKeys.memory());
    stop = startWsClient(client);
    FakeWebSocket.last.receive(helloMessage);
    await vi.advanceTimersByTimeAsync(0);
    const baseline = runs.fetches();

    FakeWebSocket.last.receive(invalidateMessage("runs"));
    await vi.advanceTimersByTimeAsync(200);
    FakeWebSocket.last.receive(invalidateMessage("runs"));
    await vi.advanceTimersByTimeAsync(100);
    FakeWebSocket.last.receive(invalidateMessage("runs"));
    await vi.advanceTimersByTimeAsync(100);
    expect(runs.fetches()).toBe(baseline);

    await vi.advanceTimersByTimeAsync(100);

    expect(runs.fetches()).toBe(baseline + 1);
    expect(memory.fetches()).toBe(1 + 1);
  });

  it("refreshes only the resources of the invalidated topics", async () => {
    const tasks = await observe(queryKeys.tasks({ folder: "done" }));
    const task = await observe(queryKeys.task("task-0001"));
    const harness = await observe(queryKeys.harness());
    stop = startWsClient(client);
    FakeWebSocket.last.receive(helloMessage);
    await vi.advanceTimersByTimeAsync(0);
    const before = [tasks.fetches(), task.fetches(), harness.fetches()];

    FakeWebSocket.last.receive(invalidateMessage("tasks"));
    await vi.advanceTimersByTimeAsync(500);

    expect([tasks.fetches(), task.fetches(), harness.fetches()]).toEqual([before[0] + 1, before[1] + 1, before[2]]);
  });

  it("ignores unknown message types and unknown topics", async () => {
    const status = await observe(queryKeys.status());
    stop = startWsClient(client);
    FakeWebSocket.last.receive(helloMessage);
    await vi.advanceTimersByTimeAsync(0);
    const baseline = status.fetches();

    FakeWebSocket.last.receive({ type: "something.new", v: 1, payload: {} });
    FakeWebSocket.last.receive("not json");
    FakeWebSocket.last.receive({ type: "invalidate", v: 1, payload: { topics: ["nonsense"] } });
    await vi.advanceTimersByTimeAsync(1000);

    expect(status.fetches()).toBe(baseline);
    expect(FakeWebSocket.last.sent).toEqual([]);
  });
});

describe("events.appended", () => {
  const row: Event = {
    id: 7,
    at: "2026-10-16T09:00:00Z",
    lane: "default",
    task: "task-0001",
    pipeline: "build",
    kind: "gate",
    stage: "test",
    from_stage: "",
    outcome: "pass",
    via: "advance",
    detail: { duration_ms: 1200 },
  };

  it("hands the rows to registered handlers without any network call", async () => {
    const api = vi.fn();
    vi.stubGlobal("fetch", api);
    const seen: Event[][] = [];
    const unsubscribe = onEventsAppended((events) => seen.push(events));
    stop = startWsClient(client);
    FakeWebSocket.last.receive(helloMessage);

    FakeWebSocket.last.receive({ type: "events.appended", v: 1, payload: { events: [row] } });
    await vi.advanceTimersByTimeAsync(1000);

    expect(seen).toEqual([[row]]);
    expect(api).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("stops calling a handler after it unsubscribes", () => {
    const handler = vi.fn();
    const unsubscribe = onEventsAppended(handler);
    unsubscribe();
    stop = startWsClient(client);

    FakeWebSocket.last.receive({ type: "events.appended", v: 1, payload: { events: [row] } });

    expect(handler).not.toHaveBeenCalled();
  });
});

describe("parseWsMessage", () => {
  it("accepts the known types and rejects malformed frames", () => {
    expect(parseWsMessage(JSON.stringify(helloMessage))?.type).toBe("hello");
    expect(parseWsMessage(JSON.stringify({ type: "hello", v: 2, payload: {} }))).toBeNull();
    expect(parseWsMessage(JSON.stringify({ type: "invalidate", v: 1, payload: {} }))).toBeNull();
    expect(parseWsMessage(JSON.stringify([1, 2]))).toBeNull();
    expect(parseWsMessage(new ArrayBuffer(2))).toBeNull();
  });
});

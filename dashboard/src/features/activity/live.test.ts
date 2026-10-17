import { describe, expect, it } from "vitest";

import { applyAppended, matchesFilters, type CachedEvents } from "@/lib/live-events";
import type { Event, EventsMeta } from "@/types/api";

function row(id: number, over: Partial<Event> = {}): Event {
  return {
    id,
    at: "2026-10-16T09:00:00Z",
    lane: "default",
    task: "task-0136",
    pipeline: "build",
    kind: "gate",
    stage: "test",
    from_stage: "",
    outcome: "pass",
    via: "",
    detail: {},
    ...over,
  };
}

function cached(rows: Event[], meta: Partial<EventsMeta> = {}): CachedEvents {
  return { data: rows, meta: { total: 40, limit: 3, offset: 0, latest_id: 40, kinds: ["gate"], ...meta } };
}

describe("matchesFilters", () => {
  it("matches every row when no filter is set", () => {
    expect(matchesFilters(row(1), {})).toBe(true);
  });

  it("matches the task as a case-insensitive substring of the task id", () => {
    expect(matchesFilters(row(1, { task: "task-0136" }), { task: "0136" })).toBe(true);
    expect(matchesFilters(row(1, { task: "task-0136" }), { task: "TASK-01" })).toBe(true);
    expect(matchesFilters(row(1, { task: "task-0137" }), { task: "0136" })).toBe(false);
  });

  it("matches the kind exactly", () => {
    expect(matchesFilters(row(1, { kind: "gate" }), { kind: "gate" })).toBe(true);
    expect(matchesFilters(row(1, { kind: "gate" }), { kind: "merged" })).toBe(false);
  });

  it("matches the result against the outcome", () => {
    expect(matchesFilters(row(1, { outcome: "fail" }), { result: "fail" })).toBe(true);
    expect(matchesFilters(row(1, { outcome: "pass" }), { result: "fail" })).toBe(false);
    expect(matchesFilters(row(1, { kind: "transition", outcome: "" }), { result: "pass" })).toBe(false);
  });

  it("needs every filter to match", () => {
    expect(matchesFilters(row(1, { kind: "gate", outcome: "fail" }), { kind: "gate", result: "pass" })).toBe(false);
  });
});

describe("applyAppended", () => {
  const page = [row(40), row(39), row(38)];

  it("prepends a matching row on page 1, trims to the page size and adds one to the total", () => {
    const next = applyAppended(cached(page), { offset: 0 }, [row(41)]);

    expect(next.data.map((r) => r.id)).toEqual([41, 40, 39]);
    expect(next.meta.total).toBe(41);
  });

  it("treats a missing offset as page 1", () => {
    expect(applyAppended(cached(page), {}, [row(41)]).data[0].id).toBe(41);
  });

  it("puts several rows newest first", () => {
    const next = applyAppended(cached(page), { offset: 0 }, [row(41), row(42)]);

    expect(next.data.map((r) => r.id)).toEqual([42, 41, 40]);
    expect(next.meta.total).toBe(42);
  });

  it("changes only the total on any other page", () => {
    const later = cached([row(37), row(36), row(35)], { offset: 3 });

    const next = applyAppended(later, { offset: 3 }, [row(41)]);

    expect(next.data).toBe(later.data);
    expect(next.meta).toEqual({ ...later.meta, total: 41, latest_id: 41 });
  });

  it("returns the same object for a row the filters exclude", () => {
    const list = cached(page);

    expect(applyAppended(list, { result: "fail" }, [row(41, { outcome: "pass" })])).toBe(list);
  });

  it("ignores a row the cached answer already covers", () => {
    const list = cached(page);

    expect(applyAppended(list, {}, [row(40), row(12)])).toBe(list);
  });
});

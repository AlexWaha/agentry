import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EventTimeline, describeEvent } from "@/components/shared/EventTimeline";
import type { Event } from "@/types/api";

function event(overrides: Partial<Event>): Event {
  return {
    id: 1,
    at: "2026-10-16T09:00:00Z",
    lane: "default",
    task: "task-0001",
    pipeline: "build",
    kind: "gate",
    stage: "test",
    from_stage: "",
    outcome: "",
    via: "advance",
    detail: {},
    ...overrides,
  };
}

const rows: Event[] = [
  event({ id: 1, kind: "registered", stage: "plan", at: "2026-10-16T08:00:00Z" }),
  event({ id: 2, kind: "gate", stage: "test", outcome: "pass", detail: { duration_ms: 90_000 }, at: "2026-10-16T08:10:00Z" }),
  event({ id: 3, kind: "gate", stage: "review", outcome: "fail", detail: { duration_ms: 400 }, at: "2026-10-16T08:20:00Z" }),
  event({ id: 4, kind: "rejected", stage: "implement", detail: { reason: "Missing edge case" }, at: "2026-10-16T08:30:00Z" }),
  event({ id: 5, kind: "approved", outcome: "commit", via: "approve", at: "2026-10-16T08:40:00Z" }),
  event({ id: 6, kind: "parked", stage: "ready", at: "2026-10-16T08:50:00Z" }),
  event({ id: 7, kind: "merged", stage: "done", at: "2026-10-16T09:00:00Z" }),
];

describe("EventTimeline", () => {
  it("renders one row per event in the order given", () => {
    render(<EventTimeline events={rows} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(7);
    expect(items.map((item) => item.getAttribute("data-kind"))).toEqual([
      "registered",
      "gate",
      "gate",
      "rejected",
      "approved",
      "parked",
      "merged",
    ]);
  });

  it("shows a gate with its result and duration", () => {
    render(<EventTimeline events={rows} />);

    const passed = screen.getByText("Gate passed: Test").closest("li") as HTMLElement;
    expect(within(passed).getByText("1m 30s")).toBeInTheDocument();
    const failed = screen.getByText("Gate failed: Review").closest("li") as HTMLElement;
    expect(within(failed).getByText("400ms")).toBeInTheDocument();
  });

  it("shows the reason of a rejection", () => {
    render(<EventTimeline events={rows} />);

    const rejected = screen.getByText(/Rejected, sent back to Implement/).closest("li") as HTMLElement;
    expect(within(rejected).getByText("Missing edge case")).toBeInTheDocument();
  });

  it("shows approvals, parks and the merge", () => {
    render(<EventTimeline events={rows} />);

    expect(screen.getByText("Approved: commit")).toBeInTheDocument();
    expect(screen.getByText("Parked at Commit approval, waiting for you")).toBeInTheDocument();
    expect(screen.getByText("Merged")).toBeInTheDocument();
  });

  it("prints the UTC time of every row", () => {
    render(<EventTimeline events={[rows[1]]} />);

    expect(screen.getByText("10-16 08:10:00Z")).toBeInTheDocument();
  });

  it("gives the list an accessible name", () => {
    render(<EventTimeline events={rows} label="History of task-0001" />);

    expect(screen.getByRole("list", { name: "History of task-0001" })).toBeInTheDocument();
  });

  it("renders an empty list without rows", () => {
    render(<EventTimeline events={[]} />);

    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });
});

describe("describeEvent", () => {
  it("falls back to the kind name and stage for kinds without a sentence", () => {
    expect(describeEvent(event({ kind: "paused", stage: "test" })).title).toBe("Paused: Test");
    expect(describeEvent(event({ kind: "closed", stage: "" })).title).toBe("Closed");
  });

  it("gives parks and blocks the attention tone and failures a distinct tone", () => {
    expect(describeEvent(event({ kind: "blocked", stage: "test" })).tone).toBe("need");
    expect(describeEvent(event({ kind: "gate", outcome: "fail" })).tone).toBe("fail");
    expect(describeEvent(event({ kind: "gate", outcome: "pass" })).tone).toBe("done");
  });
});

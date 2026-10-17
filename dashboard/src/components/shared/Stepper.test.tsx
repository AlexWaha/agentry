import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Stepper } from "@/components/shared/Stepper";
import type { Stage } from "@/types/api";

const stages: Stage[] = [
  { name: "plan", human: false, state: "done", pass: 1, fail: 0 },
  { name: "plan-approval", human: true, state: "done", pass: 0, fail: 0 },
  { name: "implement", human: false, state: "done", pass: 2, fail: 1 },
  { name: "test", human: false, state: "now", pass: 0, fail: 0 },
  { name: "review", human: false, state: "next", pass: 0, fail: 0 },
  { name: "ready", human: true, state: "next", pass: 0, fail: 0 },
  { name: "done", human: false, state: "next", pass: 0, fail: 0 },
];

function step(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
}

describe("Stepper", () => {
  it("renders one list item per stage with a readable label", () => {
    render(<Stepper stages={stages} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    expect(screen.getByText("Plan approval")).toBeInTheDocument();
    expect(screen.getByText("Commit approval")).toBeInTheDocument();
  });

  it("marks only the current stage with aria-current=step", () => {
    render(<Stepper stages={stages} />);

    expect(step("Test")).toHaveAttribute("aria-current", "step");
    expect(step("Implement")).not.toHaveAttribute("aria-current");
  });

  it("carries a text note per state so status is not color alone", () => {
    render(<Stepper stages={stages} />);

    expect(within(step("Plan approval")).getByText("Approved")).toBeInTheDocument();
    expect(within(step("Test")).getByText("In progress")).toBeInTheDocument();
    expect(within(step("Implement")).getByText("2 ok, 1 failed")).toBeInTheDocument();
    expect(within(step("Plan")).getByText("1 ok")).toBeInTheDocument();
  });

  it("says Waiting for you on a current human checkpoint and gives it the attention ring", () => {
    const waiting = stages.map((s) => (s.name === "ready" ? { ...s, state: "now" as const } : s));
    render(<Stepper stages={waiting} needsYou />);

    expect(within(step("Commit approval")).getByText("Waiting for you")).toBeInTheDocument();
    expect(step("Commit approval").querySelector("span")).toHaveClass("ring-attention-bg");
  });

  it("keeps a current human checkpoint nobody waits on out of the attention color", () => {
    const paused = stages.map((s) => (s.name === "ready" ? { ...s, state: "now" as const } : s));
    render(<Stepper stages={paused} />);

    expect(within(step("Commit approval")).queryByText("Waiting for you")).toBeNull();
    expect(step("Commit approval").querySelector("span")).not.toHaveClass("ring-attention-bg");
  });

  it("says Blocked on the current stage of a blocked run", () => {
    render(<Stepper stages={stages} blocked />);

    expect(within(step("Test")).getByText("Blocked")).toBeInTheDocument();
  });

  it("shows skipped stages as skipped", () => {
    const skipped = stages.map((s, index) => (index < 2 ? { ...s, state: "skipped" as const } : s));
    render(<Stepper stages={skipped} />);

    expect(within(step("Plan")).getByText("skipped")).toBeInTheDocument();
    expect(step("Plan")).toHaveAttribute("data-state", "skipped");
  });

  it("says Merged on a finished final stage", () => {
    const finished = stages.map((s) => ({ ...s, state: "done" as const }));
    render(<Stepper stages={finished} />);

    expect(within(step("Done")).getByText("Merged")).toBeInTheDocument();
  });

  it("says Abandoned on the final step of an abandoned run and never claims Approved or Merged", () => {
    const finished = stages.map((s) => ({ ...s, state: "done" as const }));
    render(<Stepper stages={finished} abandoned />);

    expect(within(step("Done")).getByText("Abandoned")).toBeInTheDocument();
    expect(screen.queryByText("Merged")).not.toBeInTheDocument();
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
    expect(within(step("Implement")).getByText("2 ok, 1 failed")).toBeInTheDocument();
  });

  it("pads its scroll area so the ring of the current step is not clipped", () => {
    const { container } = render(<Stepper stages={stages} />);
    const scroller = container.firstElementChild as HTMLElement;

    expect(scroller).toHaveClass("overflow-x-auto");
    expect(scroller).toHaveClass("px-2");
    expect(scroller).toHaveClass("pt-2");
    expect(step("Test").querySelector("span")).toHaveClass("ring-4");
  });

  it("falls back to the raw stage name for a stage it has no label for", () => {
    render(<Stepper stages={[{ name: "custom-gate", human: false, state: "next", pass: 0, fail: 0 }]} />);

    expect(screen.getByText("custom-gate")).toBeInTheDocument();
  });
});

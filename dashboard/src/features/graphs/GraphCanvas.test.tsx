import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { installCanvasStub, type CanvasStub } from "@/test/utils/canvas";
import { setReducedMotion } from "@/test/utils/match-media";
import { renderWithApp } from "@/test/utils/render";

import { GraphCanvas } from "./GraphCanvas";
import { createGraphEngine, type EngineOptions } from "./forceGraph";

vi.mock("./forceGraph", async (importOriginal) => {
  const actual: { createGraphEngine: typeof createGraphEngine } = await importOriginal();
  return { ...actual, createGraphEngine: vi.fn(actual.createGraphEngine) };
});

const MEMORY = memoryGraph.body.data;
const STAR = {
  nodes: [{ id: "hub", kind: "lesson", label: "hub", weight: 30 }, ...Array.from({ length: 30 }, (_, i) => ({ id: `leaf${i}`, kind: "task", label: `leaf${i}`, weight: 1 }))],
  edges: Array.from({ length: 30 }, (_, i) => ({ source: "hub", target: `leaf${i}`, kind: "task", weight: 1 })),
};

let stub: CanvasStub;

beforeEach(() => {
  stub = installCanvasStub();
  setReducedMotion(true);
});

afterEach(() => {
  stub.restore();
});

async function renderGraph(graph = MEMORY) {
  const view = await renderWithApp(<GraphCanvas title="Memory graph" nodes={graph.nodes} edges={graph.edges} />);
  const canvas = screen.getByRole("application", { name: new RegExp(`Memory graph, ${graph.nodes.length} nodes and ${graph.edges.length} edges`) });
  return { ...view, canvas };
}

const announced = (): string => document.querySelector('[aria-live="polite"].sr-only')?.textContent ?? "";
const focusCanvas = (canvas: HTMLElement): void => act(() => canvas.focus());

/** The options the component handed to the most recently created engine. */
const engineOptions = (): EngineOptions => vi.mocked(createGraphEngine).mock.calls.at(-1)?.[2] as EngineOptions;

describe("GraphCanvas", () => {
  it("is one canvas with a name that counts nodes and edges and a description that explains the controls", async () => {
    const { canvas } = await renderGraph();

    expect(canvas.tagName).toBe("CANVAS");
    expect(canvas).toHaveAccessibleDescription(/Scroll or pinch to zoom.*arrow keys/);
    expect(canvas).toHaveAttribute("tabindex", "0");
  });

  it("is a single tab stop after the two view controls", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();

    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Fit to view" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Reset view" }));
    await user.tab();
    expect(document.activeElement).toBe(canvas);
    await user.keyboard("{PageDown}{ArrowRight}{End}");
    expect(document.activeElement).toBe(canvas);
  });

  it("announces the most linked node on focus and nothing before", async () => {
    const { canvas } = await renderGraph();

    expect(announced()).toBe("");
    focusCanvas(canvas);

    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
  });

  it("does not announce or ring a node when the canvas is focused by a mouse press, until a key is used", async () => {
    const { canvas } = await renderGraph();

    fireEvent.pointerDown(canvas);
    focusCanvas(canvas);
    expect(announced()).toBe("");
    await userEvent.keyboard("{PageDown}");

    expect(announced()).toBe("Lesson: beta-lesson, 3 links");
  });

  it("walks every node with PageDown, most linked first, and Home and End jump to the ends", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();
    focusCanvas(canvas);
    const reached = [announced()];

    for (let step = 1; step < MEMORY.nodes.length; step += 1) {
      await user.keyboard("{PageDown}");
      reached.push(announced());
    }

    expect(new Set(reached).size).toBe(MEMORY.nodes.length);
    expect(reached[0]).toBe("Lesson: alpha-lesson, 3 links");
    expect(reached.at(-1)).toBe("Pattern: pattern-two, 0 links");
    await user.keyboard("{PageDown}");
    expect(announced()).toBe("Pattern: pattern-two, 0 links");
    await user.keyboard("{Home}");
    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
    await user.keyboard("{End}");
    expect(announced()).toBe("Pattern: pattern-two, 0 links");
    await user.keyboard("{PageUp}");
    expect(announced()).not.toBe("Pattern: pattern-two, 0 links");
  });

  it("moves to a neighbouring node with the arrow keys", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();
    focusCanvas(canvas);
    const first = announced();

    const seen = new Set<string>();
    for (const key of ["{ArrowRight}", "{ArrowLeft}", "{ArrowDown}", "{ArrowUp}", "{ArrowRight}", "{ArrowDown}"]) {
      await user.keyboard(key);
      seen.add(announced());
    }

    expect(seen.size).toBeGreaterThan(1);
    expect([...seen].every((text) => /^(Lesson|Pattern|Task|Agent|Area): .+, \d+ links$/.test(text))).toBe(true);
    expect(first).toBe("Lesson: alpha-lesson, 3 links");
  });

  it("selects the current node on Enter, shows its details and clears on Enter again or Escape", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();
    focusCanvas(canvas);

    await user.keyboard("{Enter}");
    const details = within(screen.getByRole("region", { name: "Selected node" }));
    expect(details.getByText("Lesson")).toBeInTheDocument();
    expect(details.getByText("alpha-lesson")).toBeInTheDocument();
    expect(details.getByText("3 links")).toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
    await user.keyboard(" ");
    expect(screen.getByRole("region", { name: "Selected node" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
  });

  it("links a selected task node to its task page", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();
    focusCanvas(canvas);
    await user.keyboard("{PageDown}{PageDown}{PageDown}");
    expect(announced()).toBe("Task: task-0106, 2 links");

    await user.keyboard("{Enter}");

    expect(screen.getByRole("link", { name: "Open task-0106" })).toHaveAttribute("href", "/tasks/task-0106");
  });

  it("offers no link for a node that is not a task", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    expect(within(screen.getByRole("region", { name: "Selected node" })).queryByRole("link")).not.toBeInTheDocument();
  });

  it("clears the selection with the Clear selection button", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));

    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
  });

  it("selects the node the engine reports on a click and clears on a click on the background", async () => {
    await renderGraph();

    act(() => engineOptions().onSelect("task:task-0107"));
    expect(within(screen.getByRole("region", { name: "Selected node" })).getByText("task-0107")).toBeInTheDocument();
    act(() => engineOptions().onSelect("task:task-0107"));
    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
    act(() => engineOptions().onSelect("lesson:2"));
    act(() => engineOptions().onSelect(null));

    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
  });

  it("resets the view and the selection with Reset view", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));

    expect(screen.queryByRole("region", { name: "Selected node" })).not.toBeInTheDocument();
  });

  it("labels the highlighted neighbours of the current node on the canvas", async () => {
    const { canvas } = await renderGraph(STAR);
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    focusCanvas(canvas);
    await waitFor(() => expect(stub.labels()).toContain("hub"));
    stub.clear();

    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(stub.labels()).toContain("leaf29"));
  });

  it("zooms and resets from the keyboard without leaving the canvas", async () => {
    const { canvas } = await renderGraph();
    const user = userEvent.setup();
    focusCanvas(canvas);

    await user.keyboard("{+}{-}{0}");

    expect(document.activeElement).toBe(canvas);
  });

  it("builds a new engine when the preference for reduced motion changes and tears the old one down", async () => {
    await renderGraph();
    const before = vi.mocked(createGraphEngine).mock.calls.length;

    act(() => setReducedMotion(false));

    expect(vi.mocked(createGraphEngine).mock.calls.length).toBe(before + 1);
    expect(engineOptions().reducedMotion).toBe(false);
  });

  it("stops its animation loop when it unmounts", async () => {
    setReducedMotion(false);
    const frames = vi.spyOn(window, "requestAnimationFrame");
    const { unmount } = await renderGraph();
    await waitFor(() => expect(frames).toHaveBeenCalled());

    unmount();
    const calls = frames.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 80));

    expect(frames.mock.calls.length).toBe(calls);
  });

  it("has no axe violation with a node selected", async () => {
    const { canvas, container } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    await expectNoAxeViolations(container);
  });
});

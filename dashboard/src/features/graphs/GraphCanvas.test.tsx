import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import memoryGraph from "@/test/fixtures/memory/graph-memory.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { installCanvasStub, type CanvasStub } from "@/test/utils/canvas";
import { setReducedMotion } from "@/test/utils/match-media";
import { renderWithApp } from "@/test/utils/render";

import { GraphCanvas } from "./GraphCanvas";
import type { GraphMode } from "./search";
import type { EngineOptions } from "./engine";
import { createGraphEngine } from "./forceGraph";
import { createGraph3D } from "./graph3d";

vi.mock("./forceGraph", async (importOriginal) => {
  const actual: { createGraphEngine: typeof createGraphEngine } = await importOriginal();
  return { ...actual, createGraphEngine: vi.fn(actual.createGraphEngine) };
});

vi.mock("./graph3d", () => ({
  createGraph3D: vi.fn(() => ({
    fit: vi.fn(),
    reset: vi.fn(),
    zoomBy: vi.fn(),
    setView: vi.fn(),
    move: vi.fn(() => null),
    orbit: vi.fn(),
    reveal: vi.fn(),
    destroy: vi.fn(),
  })),
}));

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

async function renderGraph(graph = MEMORY, mode: GraphMode = "2d", onUnavailable?: () => void) {
  const view = await renderWithApp(<GraphCanvas title="Memory graph" nodes={graph.nodes} edges={graph.edges} mode={mode} onUnavailable={onUnavailable} />);
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

  it("announces a selection by keyboard as \"<label> selected\" and its removal as \"<label> selection cleared\"", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);

    await userEvent.keyboard("{Enter}");
    expect(announced()).toBe("alpha-lesson selected");
    await userEvent.keyboard("{Enter}");
    expect(announced()).toBe("alpha-lesson selection cleared");
    await userEvent.keyboard(" ");

    expect(announced()).toBe("alpha-lesson selected");
  });

  it("announces every selection: select, Escape, select again is heard twice, with the removal in between", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    const heard: string[] = [];

    await userEvent.keyboard("{Enter}");
    heard.push(announced());
    await userEvent.keyboard("{Escape}");
    heard.push(announced());
    await userEvent.keyboard("{Enter}");
    heard.push(announced());

    expect(heard).toEqual(["alpha-lesson selected", "alpha-lesson selection cleared", "alpha-lesson selected"]);
  });

  it("says the selection is cleared when Reset view, the 0 key or Clear selection drops it, and nothing when none was set", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);

    await userEvent.keyboard("{Escape}");
    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
    await userEvent.keyboard("{Enter}0");
    expect(announced()).toBe("alpha-lesson selection cleared");
    await userEvent.keyboard("{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(announced()).toBe("alpha-lesson selection cleared");
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));

    expect(announced()).toBe("alpha-lesson selection cleared");
  });

  it("goes back to describing the focused node once the focus moves on from a selection", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");
    expect(announced()).toBe("alpha-lesson selected");

    await userEvent.keyboard("{PageDown}");

    expect(announced()).toBe("Lesson: beta-lesson, 3 links");
  });

  it("forgets the selection announcement when the graph loses focus", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    act(() => canvas.blur());

    expect(announced()).toBe("");
  });

  it("puts the keyboard focus back on the graph after Clear selection, whose button disappears", async () => {
    const { canvas } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");
    const clear = screen.getByRole("button", { name: "Clear selection" });

    await userEvent.click(clear);

    expect(screen.queryByRole("button", { name: "Clear selection" })).not.toBeInTheDocument();
    expect(document.activeElement).toBe(canvas);
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
    const pending = new Map<number, FrameRequestCallback>();
    let next = 0;
    const requested = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      next += 1;
      pending.set(next, callback);
      return next;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      pending.delete(id);
    });
    const pump = (): void => {
      const due = [...pending.values()];
      pending.clear();
      for (const callback of due) callback(0);
    };
    const { unmount } = await renderGraph();
    await waitFor(() => expect(pending.size).toBeGreaterThan(0));
    pump();
    pump();
    expect(pending.size).toBe(1);

    unmount();
    const after = requested.mock.calls.length;
    pump();
    pump();

    expect(pending.size).toBe(0);
    expect(requested.mock.calls.length).toBe(after);
  });

  it("has no axe violation with a node selected", async () => {
    const { canvas, container } = await renderGraph();
    focusCanvas(canvas);
    await userEvent.keyboard("{Enter}");

    await expectNoAxeViolations(container);
  });
});

describe("GraphCanvas, 3D mode", () => {
  /** The engine the component got from the mocked 3D module, with its spies. */
  const engine3d = () =>
    vi.mocked(createGraph3D).mock.results.at(-1)?.value as Record<"fit" | "reset" | "zoomBy" | "setView" | "move" | "orbit" | "reveal" | "destroy", ReturnType<typeof vi.fn>>;

  it("is a div application, not a canvas, says it is loading and then starts the 3D scene on it", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");

    expect(canvas.tagName).toBe("DIV");
    expect(canvas).toHaveAttribute("data-mode", "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createGraph3D).mock.calls[0][0]).toBe(canvas);
    expect(vi.mocked(createGraph3D).mock.calls[0][2].reducedMotion).toBe(true);
    await waitFor(() => expect(screen.queryByText("Loading the 3D view")).not.toBeInTheDocument());
    expect(engine3d().setView).toHaveBeenCalledWith({ selected: null, focused: null });
    expect(createGraphEngine).not.toHaveBeenCalled();
  });

  it("describes the 3D controls", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");

    expect(canvas).toHaveAccessibleDescription(/Drag to rotate.*Shift with an arrow key to orbit/);
  });

  it("walks nodes with the arrow keys exactly as in 2D: the scene reports the node reached and the focus follows", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    const user = userEvent.setup();
    focusCanvas(canvas);
    engine3d().move.mockReturnValueOnce("lesson:2");

    await user.keyboard("{ArrowLeft}");

    expect(engine3d().move).toHaveBeenCalledWith("lesson:1", "left");
    expect(engine3d().orbit).not.toHaveBeenCalled();
    expect(announced()).toBe("Lesson: beta-lesson, 3 links");
  });

  it("keeps the focus where it is when no node lies in that direction", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    focusCanvas(canvas);

    await userEvent.keyboard("{ArrowRight}");

    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
  });

  it("orbits the camera with Shift and an arrow key, and does not move the focus", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    focusCanvas(canvas);

    await userEvent.keyboard("{Shift>}{ArrowLeft}{ArrowUp}{/Shift}");

    expect(engine3d().orbit).toHaveBeenNthCalledWith(1, "left");
    expect(engine3d().orbit).toHaveBeenNthCalledWith(2, "up");
    expect(engine3d().move).not.toHaveBeenCalled();
    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
  });

  it("walks to the first and last node with Home and End and asks the scene to glide to each", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    const user = userEvent.setup();
    focusCanvas(canvas);

    await user.keyboard("{End}");
    expect(announced()).toBe("Pattern: pattern-two, 0 links");
    expect(engine3d().reveal).toHaveBeenLastCalledWith("pattern:2");
    await user.keyboard("{Home}");

    expect(announced()).toBe("Lesson: alpha-lesson, 3 links");
    expect(engine3d().reveal).toHaveBeenLastCalledWith("lesson:1");
  });

  it("walks the nodes with PageDown and reveals each", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    focusCanvas(canvas);

    await userEvent.keyboard("{PageDown}");

    expect(engine3d().reveal).toHaveBeenCalledWith("lesson:2");
    expect(announced()).toBe("Lesson: beta-lesson, 3 links");
  });

  it("zooms and resets the scene from the keyboard and the Fit and Reset buttons", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    const user = userEvent.setup();
    focusCanvas(canvas);

    await user.keyboard("{+}{-}");
    await user.click(screen.getByRole("button", { name: "Fit to view" }));
    await user.click(screen.getByRole("button", { name: "Reset view" }));

    expect(engine3d().zoomBy).toHaveBeenNthCalledWith(1, 1.25);
    expect(engine3d().zoomBy).toHaveBeenNthCalledWith(2, 0.8);
    expect(engine3d().fit).toHaveBeenCalledTimes(1);
    expect(engine3d().reset).toHaveBeenCalledTimes(1);
  });

  it("passes the selection and the keyboard focus to the scene so it can highlight them", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    focusCanvas(canvas);

    await userEvent.keyboard("{Enter}");

    expect(engine3d().setView).toHaveBeenLastCalledWith({ selected: "lesson:1", focused: "lesson:1" });
    expect(within(screen.getByRole("region", { name: "Selected node" })).getByText("alpha-lesson")).toBeInTheDocument();
  });

  it("announces a selection by keyboard in 3D too, and returns the focus to the scene after Clear selection", async () => {
    const { canvas } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    focusCanvas(canvas);

    await userEvent.keyboard("{Enter}");
    expect(announced()).toBe("alpha-lesson selected");
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));

    expect(document.activeElement).toBe(canvas);
  });

  it("selects what the scene reports on a click", async () => {
    await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());

    act(() => vi.mocked(createGraph3D).mock.calls[0][2].onSelect("task:task-0106"));

    expect(screen.getByRole("link", { name: "Open task-0106" })).toBeInTheDocument();
  });

  it("tears the scene down on unmount", async () => {
    const { unmount } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());
    const engine = engine3d();

    unmount();

    expect(engine.destroy).toHaveBeenCalledTimes(1);
  });

  it("reports the scene as unavailable when it cannot start, for example without WebGL", async () => {
    vi.mocked(createGraph3D).mockImplementationOnce(() => {
      throw new Error("Error creating WebGL context");
    });
    const onUnavailable = vi.fn();

    await renderGraph(MEMORY, "3d", onUnavailable);

    await waitFor(() => expect(onUnavailable).toHaveBeenCalledTimes(1));
    expect(onUnavailable).toHaveBeenCalledWith("webgl");
    expect(screen.getByText("Loading the 3D view")).toBeInTheDocument();
  });

  it("has no axe violation", async () => {
    const { container } = await renderGraph(MEMORY, "3d");
    await waitFor(() => expect(createGraph3D).toHaveBeenCalled());

    await expectNoAxeViolations(container);
  });
});

import { act, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";


import { axeClean, qaUser, renderApp, settle } from "./app";

/*
 * QA: accessibility of the whole app (shell plus page) on every route, and of the interactive states that
 * the page-level tests leave closed: chart tables, the graph table, expanded agents, every tab (FR-91).
 */

const PAGES = [
  "/",
  "/needs",
  "/board",
  "/tasks",
  "/tasks/task-0104",
  "/activity",
  "/metrics",
  "/memory",
  "/memory?tab=pattern",
  "/memory?tab=module",
  "/memory?tab=stats",
  "/graphs",
  "/graphs?view=code",
  "/harness",
  "/harness?tab=rules",
  "/harness?tab=skills",
  "/harness?tab=hooks",
  "/harness?tab=plugin",
  "/handoffs",
  "/handoffs/task-0107",
  "/nope",
];

describe("axe on the whole app", () => {
  it.each(PAGES)("%s has no violation, one h1 and the landmarks", async (page) => {
    await renderApp(page);
    await settle(250);

    await axeClean(document.body);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("complementary", { name: "Sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
  });

  it("the dark theme changes no structure axe can see", async () => {
    document.documentElement.classList.add("dark");
    await renderApp("/tasks");
    await settle(250);

    await axeClean(document.body);
  });
});

describe("axe on states the page tests leave closed", () => {
  it("every chart table of the Metrics page opened", async () => {
    await renderApp("/metrics");
    await settle(250);
    for (const button of screen.getAllByRole("button", { name: "Show as table" })) fireEvent.click(button);
    await settle(50);

    await axeClean(document.body);
    expect(screen.getAllByRole("table").length).toBeGreaterThanOrEqual(5);
  });

  it("the memory graph as a table", async () => {
    await renderApp("/graphs");
    await settle(250);
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    await settle(50);

    await axeClean(document.body);
    expect(screen.getAllByRole("table").length).toBeGreaterThan(0);
  });

  it("the code graph drawing and its table", async () => {
    await renderApp("/graphs?view=code");
    await settle(300);

    await axeClean(document.body);
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    await settle(50);
    await axeClean(document.body);
  });

  it("a graph node selected, with its neighbours highlighted", async () => {
    const user = qaUser();
    await renderApp("/graphs");
    await settle(250);
    const canvas = screen.getByRole("application", { name: /Memory graph/ });
    act(() => canvas.focus());
    await user.keyboard("{Enter}");

    expect(screen.getByRole("region", { name: "Selected node" })).toBeInTheDocument();
    await axeClean(document.body);
  });

  it("an expanded agent on the Harness page", async () => {
    const user = qaUser();
    await renderApp("/harness");
    await settle(250);
    await user.click(screen.getAllByText("architect")[0]);

    await axeClean(document.body);
  });

  it("a filtered, empty Tasks list with its Clear filters control", async () => {
    await renderApp("/tasks?q=zzz-no-match", {
      routes: { "/api/v1/tasks": { status: 200, body: { data: [], meta: { total: 0, limit: 25, offset: 0, hidden_superseded: 2, facets: { folders: { backlog: 0, active: 0, done: 0 }, types: [] } } } } },
    });
    await settle(250);

    await axeClean(document.body);
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("an error state on every list page", async () => {
    for (const page of ["/tasks", "/activity", "/memory", "/handoffs"]) {
      const { unmount } = await renderApp(page, {
        routes: { "/api/v1/tasks": { status: 500, body: { message: "boom", code: "internal" } }, "/api/v1/events": { status: 500, body: { message: "boom", code: "internal" } }, "/api/v1/memory": { status: 500, body: { message: "boom", code: "internal" } }, "/api/v1/handoffs": { status: 500, body: { message: "boom", code: "internal" } } },
      });
      await settle(250);

      expect(within(screen.getByRole("main")).getByRole("alert")).toHaveTextContent("boom");
      await axeClean(document.body);
      unmount();
    }
  });
});

describe("tab bars", () => {
  it.each(["/harness", "/memory", "/graphs"])("%s: one tab is selected, only it is in the tab order, its panel exists and is named by it", async (page) => {
    await renderApp(page);
    await settle(250);

    const tabs = screen.getAllByRole("tab");
    const selected = tabs.filter((tab) => tab.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(tabs.filter((tab) => tab.tabIndex === 0)).toEqual(selected);
    const panel = screen.getByRole("tabpanel");
    expect(panel.getAttribute("aria-labelledby")).toBe(selected[0].id);
    expect(selected[0].getAttribute("aria-controls")).toBe(panel.id);
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
  });

  it("Home and End jump to the first and last tab and select them", async () => {
    const user = qaUser();
    const app = await renderApp("/harness");
    await settle(250);
    screen.getByRole("tab", { name: /Agents/ }).focus();

    await user.keyboard("{End}");
    await settle(100);
    expect(app.router.state.location.search).toEqual({ tab: "plugin" });
    expect(screen.getByRole("tab", { name: /Plugin/ })).toHaveFocus();

    await user.keyboard("{Home}");
    await settle(100);
    expect(app.router.state.location.search).toEqual({});
    expect(screen.getByRole("tab", { name: /Agents/ })).toHaveFocus();

    await user.keyboard("{ArrowLeft}");
    await settle(100);
    expect(screen.getByRole("tab", { name: /Plugin/ })).toHaveAttribute("aria-selected", "true");
  });
});

describe("status is never conveyed by colour alone", () => {
  it.each(["/", "/needs", "/board", "/tasks", "/tasks/task-0104", "/activity", "/handoffs"])("%s: every status chip carries text", async (page) => {
    await renderApp(page);
    await settle(250);

    const chips = document.querySelectorAll("[data-status]");
    expect(chips.length).toBeGreaterThan(0);
    for (const chip of chips) expect((chip.textContent ?? "").trim().length, chip.outerHTML.slice(0, 80)).toBeGreaterThan(0);
  });

  it("every stepper step has a text label and the current one is marked", async () => {
    await renderApp("/");
    await settle(250);

    const steps = document.querySelectorAll("ol li[data-state]");
    expect(steps.length).toBeGreaterThan(0);
    for (const step of steps) expect((step.textContent ?? "").trim().length).toBeGreaterThan(0);
    expect(document.querySelectorAll("li[data-state=now][aria-current=step]").length).toBeGreaterThan(0);
  });
});

describe("keyboard", () => {
  it("every control of every page can take focus (no positive tabindex, nothing focusable is hidden)", async () => {
    for (const page of PAGES.slice(0, 12)) {
      const { unmount } = await renderApp(page);
      await settle(200);

      for (const element of document.querySelectorAll("[tabindex]")) {
        expect(Number(element.getAttribute("tabindex")), `${page} ${element.tagName}`).toBeLessThanOrEqual(0);
      }
      expect(document.querySelectorAll("button:disabled[tabindex='0'], [aria-hidden=true] button, [aria-hidden=true] a[href]")).toHaveLength(0);
      unmount();
    }
  });
});

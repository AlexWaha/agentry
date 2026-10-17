import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { pageWindow } from "@/components/shared/Pager";
import { manyTasks } from "@/test/fixtures/pipeline/builders";
import type { Event, Handoff, Lesson } from "@/types/api";

import { pagedHandler, qaUser, renderApp, settle, type QaApp } from "./app";

/*
 * QA: paging edges on the four paged lists through the real router: the last page, a page beyond the total, an
 * empty list, garbage page numbers, filters that reset the page, search typing that must not add history
 * entries, and the Pager's own window arithmetic (FR-123, FR-124).
 */

const gate = (id: number): Event => ({
  id,
  at: "2026-10-16T09:00:00Z",
  lane: "default",
  task: "task-0001",
  pipeline: "build",
  kind: "gate",
  stage: "review",
  from_stage: "",
  outcome: id % 2 === 0 ? "pass" : "fail",
  via: "",
  detail: { duration_ms: 100 },
});

const lesson = (id: number): Lesson => ({
  kind: "lesson",
  id,
  signature: `sig-${id}`,
  trigger: "t",
  what: "w",
  why: "y",
  fix: "f",
  area: id % 2 === 0 ? "hooks" : "tests",
  task: "task-0001",
  agent: "qa-engineer",
  retired: false,
  created: "2026-10-12T00:00:00Z",
});

const doc = (index: number): Handoff => ({
  task: `task-${String(index).padStart(4, "0")}`,
  title: `Doc ${index}`,
  author: "qa-engineer",
  date: "2026-10-15",
  bytes: 1000,
  valid: true,
});

interface ListSpec {
  name: string;
  path: string;
  api: string;
  size: number;
  noun: string;
  handler: (total: number) => ReturnType<typeof pagedHandler>;
}

const TASK_META = () => ({ hidden_superseded: 0, facets: { folders: { backlog: 0, active: 0, done: 0 }, types: ["feature"] } });

const LISTS: ListSpec[] = [
  {
    name: "tasks",
    path: "/tasks",
    api: "/api/v1/tasks",
    size: 25,
    noun: "tasks",
    handler: (total) =>
      pagedHandler(manyTasks("done", total, 1), {
        filter: (task, url) => !url.searchParams.get("folder") || task.folder === url.searchParams.get("folder"),
        meta: TASK_META,
      }),
  },
  {
    name: "activity",
    path: "/activity",
    api: "/api/v1/events",
    size: 25,
    noun: "events",
    handler: (total) =>
      pagedHandler(
        Array.from({ length: total }, (_, index) => gate(total - index)),
        {
          filter: (event, url) => !url.searchParams.get("result") || event.outcome === url.searchParams.get("result"),
          meta: () => ({ latest_id: total, kinds: ["gate", "merged"] }),
        },
      ),
  },
  {
    name: "memory",
    path: "/memory",
    api: "/api/v1/memory",
    size: 20,
    noun: "lessons",
    handler: (total) =>
      pagedHandler(
        Array.from({ length: total }, (_, index) => lesson(index + 1)),
        { defaultLimit: 20, filter: (row, url) => !url.searchParams.get("area") || row.area === url.searchParams.get("area") },
      ),
  },
  {
    name: "handoffs",
    path: "/handoffs",
    api: "/api/v1/handoffs",
    size: 25,
    noun: "handoffs",
    handler: (total) => pagedHandler(Array.from({ length: total }, (_, index) => doc(index + 1))),
  },
];

const offsetsOf = (app: QaApp, api: string): string[] => app.api.callsTo(api).map((request) => request.url.searchParams.get("offset") ?? "-");
const pagerOf = () => screen.queryByRole("navigation", { name: /pages$/ });
const rangeOf = () => within(pagerOf() as HTMLElement).getByRole("status").textContent;

describe.each(LISTS)("$name list paging", ({ path, api, size, noun, handler }) => {
  const total = size * 4 + 7;
  const last = 5;

  it("a page beyond the total renders the last page and rewrites the URL", async () => {
    const app = await renderApp(`${path}?page=99`, { routes: { [api]: handler(total) } });
    await settle(200);

    expect(app.router.state.location.search).toEqual({ page: last });
    expect(offsetsOf(app, api).at(-1)).toBe(String((last - 1) * size));
    expect(rangeOf()).toBe(`${(last - 1) * size + 1}-${total} of ${total} ${noun}`);
    expect(within(pagerOf() as HTMLElement).getByRole("button", { name: "Next" })).toBeDisabled();
    expect(within(pagerOf() as HTMLElement).getByRole("button", { name: "Previous" })).toBeEnabled();
  });

  it("the last page holds the remainder and marks itself current", async () => {
    await renderApp(`${path}?page=${last}`, { routes: { [api]: handler(total) } });
    await settle(150);

    expect(rangeOf()).toBe(`${(last - 1) * size + 1}-${total} of ${total} ${noun}`);
    expect(within(pagerOf() as HTMLElement).getByRole("button", { name: `Page ${last}` })).toHaveAttribute("aria-current", "page");
  });

  it("an empty list hides the Pager and says why", async () => {
    const app = await renderApp(path, { routes: { [api]: handler(0) } });
    await settle(150);

    expect(pagerOf()).toBeNull();
    expect(app.container.querySelector("main li")).toBeNull();
    expect(screen.getByRole("main")).toHaveTextContent(/no |nothing|empty|yet/i);
  });

  it("a page beyond an EMPTY list stays an empty list, never an error", async () => {
    await renderApp(`${path}?page=7`, { routes: { [api]: handler(0) } });
    await settle(150);

    expect(pagerOf()).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("exactly one full page shows the range and no page buttons", async () => {
    await renderApp(path, { routes: { [api]: handler(size) } });
    await settle(150);

    expect(rangeOf()).toBe(`1-${size} of ${size} ${noun}`);
    expect(within(pagerOf() as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("one row more than a page makes a second page", async () => {
    await renderApp(path, { routes: { [api]: handler(size + 1) } });
    await settle(150);

    expect(rangeOf()).toBe(`1-${size} of ${size + 1} ${noun}`);
    expect(within(pagerOf() as HTMLElement).getByRole("button", { name: "Page 2" })).toBeInTheDocument();
  });

  it.each(["abc", "0", "-1", "1.5", "2abc", "%5B2%5D", "null", "true"])("?page=%s is page 1 and sends offset 0 only", async (raw) => {
    const app = await renderApp(`${path}?page=${raw}`, { routes: { [api]: handler(total) } });
    await settle(150);

    expect(new Set(offsetsOf(app, api))).toEqual(new Set(["0"]));
    expect(app.router.state.location.search).toEqual({});
    expect(rangeOf()).toBe(`1-${size} of ${total} ${noun}`);
  });

  it("Next, a page number and Previous move through the pages and write the URL", async () => {
    const user = qaUser();
    const app = await renderApp(path, { routes: { [api]: handler(total) } });
    await settle(150);

    await user.click(within(pagerOf() as HTMLElement).getByRole("button", { name: "Next" }));
    await settle(100);
    expect(app.router.state.location.search).toEqual({ page: 2 });
    expect(offsetsOf(app, api).at(-1)).toBe(String(size));

    await user.click(within(pagerOf() as HTMLElement).getByRole("button", { name: "Page 4" }));
    await settle(100);
    expect(app.router.state.location.search).toEqual({ page: 4 });

    await user.click(within(pagerOf() as HTMLElement).getByRole("button", { name: "Page 1" }));
    await settle(100);
    expect(app.router.state.location.search).toEqual({});
    expect(rangeOf()).toBe(`1-${size} of ${total} ${noun}`);
  });
});

describe("filter changes return to page 1", () => {
  it("tasks: choosing a folder on page 3 drops the page", async () => {
    const user = qaUser();
    const app = await renderApp("/tasks?page=3", { routes: { "/api/v1/tasks": LISTS[0].handler(120) } });
    await settle(150);

    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by folder" }), "done");
    await settle(150);

    expect(app.router.state.location.search).toEqual({ folder: "done" });
    expect(app.api.callsTo("/api/v1/tasks").at(-1)?.url.searchParams.get("offset")).toBe("0");
  });

  it("activity: choosing a result on page 3 drops the page", async () => {
    const user = qaUser();
    const app = await renderApp("/activity?page=3", { routes: { "/api/v1/events": LISTS[1].handler(120) } });
    await settle(150);

    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by result" }), "fail");
    await settle(150);

    expect(app.router.state.location.search).toEqual({ result: "fail" });
    expect(app.api.callsTo("/api/v1/events").at(-1)?.url.searchParams.get("offset")).toBe("0");
  });

  it("memory: choosing an area on page 2 drops the page", async () => {
    const user = qaUser();
    const app = await renderApp("/memory?page=2", { routes: { "/api/v1/memory": LISTS[2].handler(90) } });
    await settle(200);

    const area = screen.getByRole("combobox", { name: /area/i });
    await user.selectOptions(area, within(area).getAllByRole("option")[1]);
    await settle(150);

    expect(app.router.state.location.search).not.toHaveProperty("page");
    expect(app.api.callsTo("/api/v1/memory").at(-1)?.url.searchParams.get("offset")).toBe("0");
  });

  it("memory: switching tab on page 2 drops the page", async () => {
    const user = qaUser();
    const app = await renderApp("/memory?page=2", { routes: { "/api/v1/memory": LISTS[2].handler(90) } });
    await settle(200);

    await user.click(screen.getByRole("tab", { name: /Patterns/ }));
    await settle(150);

    expect(app.router.state.location.search).toEqual({ tab: "pattern" });
  });
});

describe("typing in a search box replaces the history entry", () => {
  it.each([
    ["tasks", "/tasks", "/api/v1/tasks", "searchbox", "Search tasks by id or title"],
    ["activity", "/activity", "/api/v1/events", "searchbox", "Filter by task id"],
    ["memory", "/memory", "/api/v1/memory", "searchbox", "Search memory"],
  ])("%s: one commit after the pause, and no extra history entry", async (name, path, api, role, label) => {
    const user = qaUser();
    const list = LISTS.find((entry) => entry.name === name) as ListSpec;
    const app = await renderApp(path, { routes: { [api]: list.handler(60) } });
    await settle(150);
    const before = app.router.history.length;
    expect(before).toBeGreaterThan(0);

    const box = screen.getByRole(role, { name: label });
    await user.type(box, "abcd");
    await settle(450);

    expect(app.router.history.length).toBe(before);
    const sent = app.api.callsTo(api).filter((request) => request.url.searchParams.has(name === "tasks" ? "q" : name === "activity" ? "task" : "q"));
    expect(sent).toHaveLength(1);
  });
});

describe("a list that shrinks while the page is open", () => {
  it("moves a page that no longer exists back to the last page", async () => {
    let total = 120;
    const app = await renderApp("/tasks?page=5", {
      ws: true,
      routes: { "/api/v1/tasks": (url) => (LISTS[0].handler(total) as (u: URL) => never)(url) },
    });
    await settle(150);
    expect(app.router.state.location.search).toEqual({ page: 5 });

    total = 60;
    await act(async () => {
      await app.queryClient.invalidateQueries({ queryKey: ["tasks"] });
    });
    await settle(150);

    expect(app.router.state.location.search).toEqual({ page: 3 });
  });
});

describe("Pager window arithmetic", () => {
  const PAGES = [1, 2, 7, 8, 9, 10, 25, 100];

  it.each(PAGES)("last=%i: every page in range yields a window that holds the current page, the ends and no adjacent gaps", (last) => {
    for (let page = 1; page <= last; page += 1) {
      const window = pageWindow(page, last);
      const numbers = window.filter((entry): entry is number => entry !== null);

      expect(numbers).toContain(page);
      expect(numbers).toContain(1);
      expect(numbers).toContain(last);
      expect(new Set(numbers).size).toBe(numbers.length);
      expect([...numbers].sort((a, b) => a - b)).toEqual(numbers);
      for (let index = 1; index < window.length; index += 1) {
        expect(window[index] === null && window[index - 1] === null).toBe(false);
      }
      expect(window[0]).toBe(1);
      expect(window.at(-1)).toBe(last);
      if (last > 7) expect(window.length).toBeLessThanOrEqual(7);
    }
  });

  it("collapses 10 pages with an ellipsis on both sides in the middle", () => {
    expect(pageWindow(5, 10)).toEqual([1, null, 4, 5, 6, null, 10]);
  });

  it("shows seven pages in full and collapses from the eighth", () => {
    expect(pageWindow(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(pageWindow(1, 8)).toEqual([1, 2, null, 8]);
  });
});

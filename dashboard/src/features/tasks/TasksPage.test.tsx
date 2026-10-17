import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { startWsClient } from "@/lib/ws-client";
import { fromFixture, manyTasks, summary, tasksAnswer } from "@/test/fixtures/pipeline/builders";
import epicsFixture from "@/test/fixtures/pipeline/epics.json";
import tasksFixture from "@/test/fixtures/pipeline/tasks-list.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok, type MockHandler } from "@/test/utils/api-mock";
import { FakeWebSocket, helloMessage, installFakeWebSocket, invalidateMessage } from "@/test/utils/fake-ws";
import { renderWithApp } from "@/test/utils/render";
import type { Folder, TaskSummary } from "@/types/api";

import { validateTasksSearch } from "./search";
import { TasksPage } from "./TasksPage";

const TASKS = "/api/v1/tasks";
const EPICS = "/api/v1/epics";
const DEBOUNCE_MS = 300;

/** A /tasks handler that filters and pages `all` like the real server (FR-21). */
function serverOf(all: TaskSummary[], hidden = 0): MockHandler {
  return (url) => {
    const get = (name: string) => url.searchParams.get(name);
    const visible = all.filter(
      (task) =>
        (!get("type") || task.type === get("type")) &&
        (!get("epic") || task.epic === get("epic")) &&
        (!get("q") || `${task.id} ${task.title}`.toLowerCase().includes((get("q") ?? "").toLowerCase())),
    );
    const rows = visible.filter((task) => !get("folder") || task.folder === get("folder"));
    const count = (folder: Folder) => visible.filter((task) => task.folder === folder).length;
    const limit = Number(get("limit") ?? 25);
    const offset = Number(get("offset") ?? 0);
    return tasksAnswer(rows.slice(offset, offset + limit), rows.length, {
      limit,
      offset,
      hidden_superseded: get("include_superseded") === "true" ? 0 : hidden,
      facets: { folders: { backlog: count("backlog"), active: count("active"), done: count("done") }, types: ["bugfix", "feature"] },
    });
  };
}

const FIXTURE_TASKS = tasksFixture.body.data as unknown as TaskSummary[];

function render(url = "/tasks", shell = false) {
  return renderWithApp(<TasksPage />, { url, path: "/tasks", validateSearch: validateTasksSearch, shell });
}

const searchesOf = (api: ReturnType<typeof mockApi>) => api.callsTo(TASKS).map((request) => request.url.searchParams);

afterEach(() => {
  vi.useRealTimers();
});

describe("TasksPage", () => {
  it("shows a loading state while the list is on its way", async () => {
    mockApi({ [TASKS]: fromFixture(tasksFixture), [EPICS]: fromFixture(epicsFixture) });
    await render();

    expect(screen.getByRole("status")).toHaveTextContent("Loading tasks");
    expect(await screen.findByRole("list", { name: "Tasks" })).toBeInTheDocument();
  });

  it("renders a row with its status chip, id and title links, and the priority, type and epic chips", async () => {
    const api = mockApi({ [TASKS]: fromFixture(tasksFixture), [EPICS]: fromFixture(epicsFixture) });
    await render();

    const rows = within(await screen.findByRole("list", { name: "Tasks" })).getAllByRole("listitem");
    expect(rows).toHaveLength(FIXTURE_TASKS.length);
    const delta = rows.find((row) => row.textContent?.includes("task-0104")) as HTMLElement;
    expect(delta.querySelector("[data-status]")).toHaveAttribute("data-status", "active");
    expect(within(delta).getByText("Active")).toBeInTheDocument();
    expect(within(delta).getByText("high")).toBeInTheDocument();
    expect(within(delta).getByText("feature")).toBeInTheDocument();
    expect(within(delta).getByText("epic-0001")).toBeInTheDocument();
    expect(within(delta).getByRole("link", { name: "task-0104" })).toHaveAttribute("href", "/tasks/task-0104");
    expect(within(delta).getByRole("link", { name: "Delta in flight" })).toHaveAttribute("href", "/tasks/task-0104");
    expect(searchesOf(api)[0].toString()).toBe("limit=25&offset=0");
  });

  it("labels the folder options with the facet counts and lists the types and epics", async () => {
    mockApi({ [TASKS]: fromFixture(tasksFixture), [EPICS]: fromFixture(epicsFixture) });
    await render();

    await screen.findByRole("list", { name: "Tasks" });
    expect(screen.getByRole("option", { name: "All folders" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Backlog (2)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Active (2)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Done (4)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "All types" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "refactor" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "epic-0002" })).toBeInTheDocument();
  });

  it("sends q 300 ms after the last keystroke and keeps one history entry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi({ [TASKS]: serverOf(FIXTURE_TASKS), [EPICS]: fromFixture(epicsFixture) });
    const { router } = await render();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("list", { name: "Tasks" });
    const before = router.history.length;

    await user.type(screen.getByRole("searchbox", { name: "Search tasks by id or title" }), "zeta");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 50);
    });
    expect(searchesOf(api).some((params) => params.has("q"))).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    await waitFor(() => expect(searchesOf(api).some((params) => params.get("q") === "zeta")).toBe(true));
    expect(router.state.location.search).toEqual({ q: "zeta" });
    expect(router.history.length).toBe(before);
    expect(await screen.findByText("Zeta done")).toBeInTheDocument();
    expect(screen.queryByText("Alpha feature")).not.toBeInTheDocument();
  });

  it("sends the folder, type and epic selects and resets the page", async () => {
    const api = mockApi({ [TASKS]: serverOf(FIXTURE_TASKS), [EPICS]: fromFixture(epicsFixture) });
    const { router } = await render("/tasks?page=2");
    await screen.findByRole("option", { name: "epic-0002" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by folder" }), "done");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by type" }), "bugfix");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by epic" }), "epic-0002");

    await waitFor(() => {
      const last = searchesOf(api).at(-1);
      expect(last?.get("folder")).toBe("done");
      expect(last?.get("type")).toBe("bugfix");
      expect(last?.get("epic")).toBe("epic-0002");
    });
    expect(router.state.location.search).toEqual({ folder: "done", type: "bugfix", epic: "epic-0002" });
  });

  it("shows Clear filters only with a filter set and resets all of them and the page", async () => {
    const api = mockApi({ [TASKS]: serverOf(FIXTURE_TASKS), [EPICS]: fromFixture(epicsFixture) });
    const none = await render();
    await screen.findByRole("list", { name: "Tasks" });
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    none.unmount();

    const { router } = await render("/tasks?folder=done&type=feature&epic=epic-0001&q=zeta&page=2");
    await userEvent.click(await screen.findByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
    await waitFor(() => expect(searchesOf(api).at(-1)?.toString()).toBe("limit=25&offset=0"));
    expect(screen.getByRole("searchbox", { name: "Search tasks by id or title" })).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("shows the superseded note and a toggle that requests include_superseded", async () => {
    const api = mockApi({ [TASKS]: serverOf(FIXTURE_TASKS, 1), [EPICS]: ok([]) });
    const { router } = await render();

    expect(await screen.findByText("1 superseded hidden")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Show superseded tasks" }));

    await waitFor(() => expect(searchesOf(api).at(-1)?.get("include_superseded")).toBe("true"));
    expect(router.state.location.search).toEqual({ superseded: 1 });
    expect(await screen.findByText("0 superseded hidden")).toBeInTheDocument();
  });

  it("says No tasks match with a hint and hides the pager when nothing matches", async () => {
    mockApi({ [TASKS]: serverOf([]), [EPICS]: ok([]) });
    await render("/tasks?q=nothing");

    expect(await screen.findByText("No tasks match.")).toBeInTheDocument();
    expect(screen.getByText(/Try other words or clear the filters/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "tasks pages" })).not.toBeInTheDocument();
  });

  it("shows the error with a retry that loads the list", async () => {
    const api = mockApi({ [TASKS]: fail(500, "internal", "Store failed"), [EPICS]: ok([]) });
    await render();

    expect(await screen.findByRole("alert")).toHaveTextContent("Store failed");
    api.set(TASKS, serverOf(FIXTURE_TASKS));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("list", { name: "Tasks" })).toBeInTheDocument();
  });

  describe("paging (FR-124)", () => {
    const SIXTY = manyTasks("done", 60, 1);

    it("asks for the page in the URL and shows its range", async () => {
      const api = mockApi({ [TASKS]: serverOf(SIXTY), [EPICS]: ok([]) });
      await render("/tasks?page=2");

      expect(await screen.findByText("26-50 of 60 tasks")).toBeInTheDocument();
      expect(searchesOf(api)[0].get("offset")).toBe("25");
      expect(screen.getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
    });

    it("moves to another page through the pager and updates the URL", async () => {
      mockApi({ [TASKS]: serverOf(SIXTY), [EPICS]: ok([]) });
      const { router } = await render();
      await screen.findByText("1-25 of 60 tasks");

      await userEvent.click(screen.getByRole("button", { name: "Page 3" }));

      expect(await screen.findByText("51-60 of 60 tasks")).toBeInTheDocument();
      expect(router.state.location.search).toEqual({ page: 3 });
    });

    it("renders the last page and corrects the URL when the page is beyond the end", async () => {
      mockApi({ [TASKS]: serverOf(SIXTY), [EPICS]: ok([]) });
      const { router } = await render("/tasks?page=99");

      expect(await screen.findByText("51-60 of 60 tasks")).toBeInTheDocument();
      await waitFor(() => expect(router.state.location.search).toEqual({ page: 3 }));
    });

    it("renders page 1 for a page that is not a number", async () => {
      mockApi({ [TASKS]: serverOf(SIXTY), [EPICS]: ok([]) });
      await render("/tasks?page=abc");

      expect(await screen.findByText("1-25 of 60 tasks")).toBeInTheDocument();
    });

    it("shows the folder count of a Board link as its total", async () => {
      mockApi({ [TASKS]: serverOf([...manyTasks("done", 30, 200), ...manyTasks("backlog", 15, 1)]), [EPICS]: ok([]) });
      await render("/tasks?folder=done");

      expect(await screen.findByText("1-25 of 30 tasks")).toBeInTheDocument();
    });
  });

  it("is reachable from the Tasks entry of the sidebar and has no axe violation", async () => {
    mockApi({ [TASKS]: fromFixture(tasksFixture), [EPICS]: fromFixture(epicsFixture) });
    await render("/tasks", true);

    await screen.findByRole("list", { name: "Tasks" });
    expect(within(screen.getByRole("navigation", { name: "Views" })).getByRole("link", { name: "Tasks" })).toHaveAttribute("href", "/tasks");
    await expectNoAxeViolations(document.body);
  });

  it("refetches on an invalidate message and keeps the filter text and the scroll position", async () => {
    installFakeWebSocket();
    const api = mockApi({ [TASKS]: serverOf([summary("task-0001", "done")]), [EPICS]: ok([]) });
    const scrollTo = vi.spyOn(window, "scrollTo");
    const { queryClient } = await render("/tasks?type=feature");
    const stop = startWsClient(queryClient);
    try {
      await screen.findByText("Title of task-0001");
      FakeWebSocket.last.open();
      FakeWebSocket.last.receive(helloMessage);
      await waitFor(() => expect(api.callsTo(TASKS).length).toBeGreaterThanOrEqual(2));
      api.set(TASKS, serverOf([summary("task-0001", "done"), summary("task-0002", "done", { title: "Fresh arrival" })]));
      Object.defineProperty(window, "scrollY", { value: 240, configurable: true });
      scrollTo.mockClear();

      FakeWebSocket.last.receive(invalidateMessage("tasks"));

      expect(await screen.findByText("Fresh arrival", undefined, { timeout: 3000 })).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Filter by type" })).toHaveValue("feature");
      expect(window.scrollY).toBe(240);
      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      stop();
    }
  });
});

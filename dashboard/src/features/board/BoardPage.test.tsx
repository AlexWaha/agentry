import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { startWsClient } from "@/lib/ws-client";
import { fromFixture, manyTasks, summary, tasksAnswer } from "@/test/fixtures/pipeline/builders";
import epicsFixture from "@/test/fixtures/pipeline/epics.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok, type MockResponse } from "@/test/utils/api-mock";
import { FakeWebSocket, helloMessage, installFakeWebSocket, invalidateMessage } from "@/test/utils/fake-ws";
import { renderWithApp } from "@/test/utils/render";
import type { Folder, TaskSummary } from "@/types/api";

import { BoardPage } from "./BoardPage";
import { validateBoardSearch } from "./search";

const TASKS = "/api/v1/tasks";
const EPICS = "/api/v1/epics";

interface Column {
  rows: TaskSummary[];
  total: number;
  hidden?: number;
}

/** A /tasks handler that answers each folder from `columns`, as the real server does. */
function tasksByFolder(columns: Record<Folder, Column>): (url: URL) => MockResponse {
  return (url) => {
    const column = columns[url.searchParams.get("folder") as Folder];
    return tasksAnswer(column.rows.slice(0, 12), column.total, { limit: 12, hidden_superseded: column.hidden ?? 0 });
  };
}

const SMALL: Record<Folder, Column> = {
  backlog: { rows: [summary("task-0101", "backlog", { epic: "epic-0001", priority: "high" })], total: 1, hidden: 1 },
  active: { rows: [summary("task-0104", "active", { epic: "epic-0001", assignee: "senior-frontend-dev" })], total: 1 },
  done: { rows: [summary("task-0106", "done", { type: "bugfix" })], total: 4, hidden: 1 },
};

function render(url = "/board", shell = false) {
  return renderWithApp(<BoardPage />, { url, path: "/board", validateSearch: validateBoardSearch, shell });
}

const region = (name: string) => within(screen.getByRole("region", { name }));

describe("BoardPage", () => {
  it("shows a loading state in every column while the cards are on their way", async () => {
    mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    await render();

    expect(screen.getAllByRole("status")).toHaveLength(3);
    expect(await screen.findByText("task-0101")).toBeInTheDocument();
  });

  it("requests the three folders capped at twelve and shows each total in its header", async () => {
    const api = mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    await render();

    await screen.findByText("task-0106");
    const searches = api.callsTo(TASKS).map((request) => request.url.search);
    expect(searches.sort()).toEqual(["?folder=active&limit=12", "?folder=backlog&limit=12", "?folder=done&limit=12"]);
    expect(region("Backlog").getByRole("heading")).toHaveTextContent("1");
    expect(region("Active").getByRole("heading")).toHaveTextContent("1");
    expect(region("Done").getByRole("heading")).toHaveTextContent("4");
  });

  it("shows id, title, type, priority, epic and assignee on a card and no Load more control", async () => {
    mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    await render();

    const card = (await screen.findByRole("link", { name: "Title of task-0104" })).closest("article");
    expect(card).not.toBeNull();
    const inCard = within(card as HTMLElement);
    expect(inCard.getByText("task-0104")).toBeInTheDocument();
    expect(inCard.getByText("feature")).toBeInTheDocument();
    expect(inCard.getByText("medium")).toBeInTheDocument();
    expect(inCard.getByText("epic-0001")).toBeInTheDocument();
    expect(inCard.getByText("senior-frontend-dev")).toBeInTheDocument();
    expect(region("Backlog").getByText("high")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Title of task-0104" })).toHaveAttribute("href", "/tasks/task-0104");
    expect(screen.queryByText(/load more/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("sums the superseded tasks hidden over the three columns and re-requests all three with the toggle", async () => {
    const api = mockApi({
      [TASKS]: (url) => {
        const shown = url.searchParams.get("include_superseded") === "true";
        return tasksByFolder(shown ? { ...SMALL, backlog: { ...SMALL.backlog, hidden: 0 }, done: { ...SMALL.done, hidden: 0 } } : SMALL)(url);
      },
      [EPICS]: fromFixture(epicsFixture),
    });
    const { router } = await render();

    expect(await screen.findByText("2 superseded hidden")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Show superseded tasks" }));

    await waitFor(() => expect(api.callsTo(TASKS).filter((request) => request.url.searchParams.get("include_superseded") === "true")).toHaveLength(3));
    expect(router.state.location.search).toEqual({ superseded: 1 });
    expect(await screen.findByText("0 superseded hidden")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Show superseded tasks" })).toBeChecked();
  });

  it("adds the selected epic to all three requests", async () => {
    const api = mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    await render();

    await screen.findByText("task-0101");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by epic" }), "epic-0002");

    await waitFor(() => expect(api.callsTo(TASKS).filter((request) => request.url.searchParams.get("epic") === "epic-0002")).toHaveLength(3));
    expect(api.callsTo(TASKS).filter((request) => request.url.searchParams.get("epic") === "epic-0002").map((request) => request.url.searchParams.get("folder")).sort()).toEqual([
      "active",
      "backlog",
      "done",
    ]);
  });

  describe("capped columns (FR-125)", () => {
    const BIG: Record<Folder, Column> = {
      backlog: { rows: manyTasks("backlog", 15, 1), total: 15 },
      active: { rows: manyTasks("active", 3, 100), total: 3 },
      done: { rows: manyTasks("done", 30, 200), total: 30 },
    };

    it("shows Showing 12 of N and View all (N) only in the columns above the cap", async () => {
      mockApi({ [TASKS]: tasksByFolder(BIG), [EPICS]: ok([]) });
      await render();

      await screen.findByText("task-0200");
      expect(region("Done").getByText("Showing 12 of 30")).toBeInTheDocument();
      expect(region("Done").getAllByRole("listitem")).toHaveLength(12);
      expect(region("Done").getByRole("link", { name: "View all (30)" })).toHaveAttribute("href", "/tasks?folder=done");
      expect(region("Backlog").getByText("Showing 12 of 15")).toBeInTheDocument();
      expect(region("Backlog").getByRole("link", { name: "View all (15)" })).toHaveAttribute("href", "/tasks?folder=backlog");
      expect(region("Active").queryByText(/Showing/)).not.toBeInTheDocument();
      expect(region("Active").queryByRole("link", { name: /View all/ })).not.toBeInTheDocument();
    });

    it("carries the epic and the superseded toggle into the View all link", async () => {
      mockApi({ [TASKS]: tasksByFolder(BIG), [EPICS]: ok([{ id: "epic-0005", title: "Dashboard", status: "active", tasks: [] }]) });
      await render("/board?epic=epic-0005");

      expect(await region("Done").findByRole("link", { name: "View all (30)" })).toHaveAttribute("href", "/tasks?folder=done&epic=epic-0005");
    });

    it("adds superseded=1 when the toggle is on", async () => {
      mockApi({ [TASKS]: tasksByFolder(BIG), [EPICS]: ok([]) });
      await render("/board?epic=epic-0005&superseded=1");

      expect(await region("Done").findByRole("link", { name: "View all (30)" })).toHaveAttribute(
        "href",
        "/tasks?folder=done&epic=epic-0005&superseded=1",
      );
    });
  });

  it("says a column is empty and shows no footer", async () => {
    const empty: Column = { rows: [], total: 0 };
    mockApi({ [TASKS]: tasksByFolder({ backlog: empty, active: empty, done: empty }), [EPICS]: ok([]) });
    await render();

    expect(await screen.findAllByText("Nothing here.")).toHaveLength(3);
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });

  it("shows an error with a retry in a failed column and keeps the others", async () => {
    const api = mockApi({
      [TASKS]: (url) => (url.searchParams.get("folder") === "done" ? fail(500, "internal", "Done store failed") : tasksByFolder(SMALL)(url)),
      [EPICS]: fromFixture(epicsFixture),
    });
    await render();

    expect(await region("Done").findByRole("alert")).toHaveTextContent("Done store failed");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(await screen.findByText("task-0101")).toBeInTheDocument();

    api.set(TASKS, tasksByFolder(SMALL));
    await userEvent.click(region("Done").getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("task-0106")).toBeInTheDocument();
  });

  it("has no axe violation in the data state", async () => {
    mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    await render("/board", true);

    await screen.findByText("task-0106");
    await expectNoAxeViolations(document.body);
  });

  it("refetches on an invalidate message and leaves the scroll position and the epic filter alone", async () => {
    installFakeWebSocket();
    const api = mockApi({ [TASKS]: tasksByFolder(SMALL), [EPICS]: fromFixture(epicsFixture) });
    const scrollTo = vi.spyOn(window, "scrollTo");
    const { queryClient } = await render("/board?epic=epic-0001");
    const stop = startWsClient(queryClient);
    try {
      await screen.findByText("task-0101");
      FakeWebSocket.last.open();
      FakeWebSocket.last.receive(helloMessage);
      await waitFor(() => expect(api.callsTo(TASKS).length).toBeGreaterThanOrEqual(6));
      api.set(TASKS, tasksByFolder({ ...SMALL, backlog: { rows: [summary("task-0999", "backlog")], total: 1 } }));
      Object.defineProperty(window, "scrollY", { value: 180, configurable: true });
      scrollTo.mockClear();

      FakeWebSocket.last.receive(invalidateMessage("tasks"));

      expect(await screen.findByText("task-0999", undefined, { timeout: 3000 })).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Filter by epic" })).toHaveValue("epic-0001");
      expect(window.scrollY).toBe(180);
      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      stop();
    }
  });
});

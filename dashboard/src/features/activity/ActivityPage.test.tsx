import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ROUTES } from "@/config/routes";
import { createQueryClient } from "@/lib/query-client";
import { startWsClient } from "@/lib/ws-client";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { FakeWebSocket, installFakeWebSocket } from "@/test/utils/fake-ws";
import { renderWithApp } from "@/test/utils/render";
import type { Event } from "@/types/api";

import eventsFixture from "@/test/fixtures/activity/events.json";
import { ActivityPage } from "./ActivityPage";
import { validateActivitySearch } from "./search";

const KINDS = eventsFixture.body.meta.kinds;
const EVENTS_PATH = "/api/v1/events";

function row(id: number, over: Partial<Event> = {}): Event {
  return { ...eventsFixture.body.data[2], id, task: "task-0136", kind: "transition", outcome: "", ...over };
}

/** A journal of `count` rows, newest (highest id) first; gates alternate pass and fail, tasks cycle over five ids. */
function journal(count: number): Event[] {
  return Array.from({ length: count }, (_, index) => {
    const id = count - index;
    const task = `task-010${id % 5}`;
    if (id % 3 === 0) return row(id, { task, kind: "gate", stage: "test", outcome: id % 2 === 0 ? "pass" : "fail" });
    if (id % 3 === 1) return row(id, { task, kind: "transition", stage: "review", from_stage: "test" });
    return row(id, { task, kind: "approved", stage: "ready", outcome: "commit", via: "user" });
  });
}

/** Answers like the server: the filters first, then the page, with the exact total of the filtered list. */
function serve(all: Event[]) {
  return (url: URL) => {
    const task = url.searchParams.get("task")?.toLowerCase() ?? "";
    const kind = url.searchParams.get("kind") ?? "";
    const result = url.searchParams.get("result") ?? "";
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    const matching = all.filter(
      (event) => event.task.toLowerCase().includes(task) && (!kind || event.kind === kind) && (!result || event.outcome === result),
    );
    return ok(matching.slice(offset, offset + limit), {
      total: matching.length,
      limit,
      offset,
      latest_id: all[0]?.id ?? 0,
      kinds: KINDS,
    });
  };
}

async function renderPage(url = "/activity", queryClient = createQueryClient()) {
  return renderWithApp(<ActivityPage />, { url, path: ROUTES.activity, validateSearch: validateActivitySearch, queryClient });
}

function feedRows(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Journal events" })).getAllByRole("listitem");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Activity states", () => {
  it("shows a loading status while the first answer is on its way", async () => {
    vi.stubGlobal("fetch", () => new Promise(() => undefined));

    await renderPage();

    expect(screen.getByRole("status", { name: "Loading events" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Journal events" })).not.toBeInTheDocument();
  });

  it("shows the empty state when the journal has no events", async () => {
    mockApi({ [EVENTS_PATH]: serve([]) });

    await renderPage();

    expect(await screen.findByText("No events recorded yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "events pages" })).not.toBeInTheDocument();
  });

  it("explains an empty filtered list and offers to clear the filters", async () => {
    mockApi({ [EVENTS_PATH]: serve(journal(30)) });

    await renderPage("/activity?task=9999");

    expect(await screen.findByText("No events match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("shows the error state and loads the list when Retry succeeds", async () => {
    const api = mockApi({ [EVENTS_PATH]: fail(500, "server_error", "The journal could not be read.") });
    await renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("The journal could not be read.");

    api.set(EVENTS_PATH, serve(journal(30)));
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("list", { name: "Journal events" })).toBeInTheDocument();
  });

  it("shows the rows, the pager and passes axe in the data state", async () => {
    mockApi({ [EVENTS_PATH]: serve(journal(120)) });

    const { container } = await renderPage();
    await screen.findByRole("list", { name: "Journal events" });

    expect(feedRows()).toHaveLength(25);
    expect(feedRows()[0]).toHaveTextContent("task-0100");
    expect(screen.getByText("1-25 of 120 events")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("describes a failed gate with its stage and a link to the task", async () => {
    mockApi({ [EVENTS_PATH]: serve([row(9, { kind: "gate", stage: "review", outcome: "fail", task: "task-0136", detail: { duration_ms: 8420 } })]) });

    await renderPage();
    const [first] = await screen.findAllByRole("listitem");

    expect(first).toHaveTextContent("Gate failed: Review");
    expect(first).toHaveTextContent("8s");
    expect(within(first).getByRole("link", { name: "task-0136" })).toHaveAttribute("href", "/tasks/task-0136");
  });
});

describe("Activity paging and filters", () => {
  it("asks for 25 rows per page and moves through the pages in the URL", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    const { router } = await renderPage();
    await screen.findByRole("list", { name: "Journal events" });

    expect(api.requests[0].url.searchParams.get("limit")).toBe("25");
    expect(api.requests[0].url.searchParams.get("offset")).toBe("0");

    await userEvent.setup().click(screen.getByRole("button", { name: "Page 2" }));

    await waitFor(() => expect(screen.getByText("26-50 of 120 events")).toBeInTheDocument());
    expect(api.requests.at(-1)?.url.searchParams.get("offset")).toBe("25");
    expect(router.state.location.search).toEqual({ page: 2 });
  });

  it("restores the page from the URL and corrects a page beyond the last one", async () => {
    mockApi({ [EVENTS_PATH]: serve(journal(120)) });

    const { router } = await renderPage("/activity?page=9");

    await waitFor(() => expect(router.state.location.search).toEqual({ page: 5 }));
    await waitFor(() => expect(screen.getByText("101-120 of 120 events")).toBeInTheDocument());
  });

  it("sends the task text after 300 ms of quiet", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    const { router } = await renderPage();
    await screen.findByRole("list", { name: "Journal events" });
    const requestsBefore = api.requests.length;

    await userEvent.setup().type(screen.getByRole("searchbox", { name: "Filter by task id" }), "0103");

    expect(api.requests).toHaveLength(requestsBefore);
    await waitFor(() => expect(api.requests.at(-1)?.url.searchParams.get("task")).toBe("0103"));
    expect(router.state.location.search).toEqual({ task: "0103" });
    await waitFor(() => expect(feedRows().every((item) => item.textContent?.includes("task-0103"))).toBe(true));
  });

  it("offers the event types of meta.kinds and sends the chosen one", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    await renderPage();
    await screen.findByRole("list", { name: "Journal events" });
    const select = screen.getByRole("combobox", { name: "Filter by event type" });

    expect(within(select).getAllByRole("option")).toHaveLength(KINDS.length + 1);
    expect(within(select).getByRole("option", { name: "All event types" })).toBeInTheDocument();

    await userEvent.setup().selectOptions(select, "Gate");

    await waitFor(() => expect(api.requests.at(-1)?.url.searchParams.get("kind")).toBe("gate"));
    await waitFor(() => expect(feedRows().every((item) => item.textContent?.includes("Gate"))).toBe(true));
  });

  it("sends the chosen result", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    await renderPage();
    await screen.findByRole("list", { name: "Journal events" });
    const select = screen.getByRole("combobox", { name: "Filter by result" });

    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["Any result", "Pass", "Fail"]);

    await userEvent.setup().selectOptions(select, "Fail");

    await waitFor(() => expect(api.requests.at(-1)?.url.searchParams.get("result")).toBe("fail"));
  });

  it("returns to page 1 when a filter changes", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    const { router } = await renderPage("/activity?page=3");
    await screen.findByRole("list", { name: "Journal events" });

    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Filter by result" }), "Fail");

    await waitFor(() => expect(router.state.location.search).toEqual({ result: "fail" }));
    expect(api.requests.at(-1)?.url.searchParams.get("offset")).toBe("0");
  });

  it("shows Clear filters only while a filter is set and resets the three filters and the page", async () => {
    const api = mockApi({ [EVENTS_PATH]: serve(journal(120)) });
    const { router } = await renderPage("/activity?task=0100&kind=gate&result=fail&page=2");
    const taskBox = await screen.findByRole("searchbox", { name: "Filter by task id" });

    await userEvent.setup().click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument());
    expect(taskBox).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Filter by event type" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Filter by result" })).toHaveValue("");
    const last = api.requests.at(-1)?.url.searchParams;
    expect([last?.get("task"), last?.get("kind"), last?.get("result"), last?.get("offset")]).toEqual([null, null, null, "0"]);
  });

  it("restores the filters from the URL", async () => {
    mockApi({ [EVENTS_PATH]: serve(journal(120)) });

    await renderPage("/activity?task=0101&kind=gate&result=pass");

    expect(await screen.findByRole("searchbox", { name: "Filter by task id" })).toHaveValue("0101");
    expect(screen.getByRole("combobox", { name: "Filter by event type" })).toHaveValue("gate");
    expect(screen.getByRole("combobox", { name: "Filter by result" })).toHaveValue("pass");
  });
});

describe("Activity live rows (FR-100)", () => {
  /** Delivers the rows, then lets the query cache notify its observers (it does so on a zero-delay timer). */
  async function appended(...events: Event[]) {
    await act(async () => {
      FakeWebSocket.last.receive({ type: "events.appended", v: 1, payload: { events } });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function renderLive(url: string, total = 120) {
    installFakeWebSocket();
    const api = mockApi({ [EVENTS_PATH]: serve(journal(total)) });
    const queryClient = createQueryClient();
    const stop = startWsClient(queryClient);
    const rendered = await renderPage(url, queryClient);
    return { api, stop, ...rendered };
  }

  it("prepends a matching row on page 1, keeps 25 rows, adds one to the total and sends no request", async () => {
    const { api, stop } = await renderLive("/activity");
    await screen.findByRole("list", { name: "Journal events" });
    const requestsBefore = api.requests.length;

    await appended(row(121, { task: "task-0200", kind: "gate", stage: "review", outcome: "fail" }));

    expect(feedRows()).toHaveLength(25);
    expect(feedRows()[0]).toHaveTextContent("task-0200");
    expect(feedRows()[0]).toHaveTextContent("Gate failed: Review");
    expect(screen.getByText("1-25 of 121 events")).toBeInTheDocument();
    expect(api.requests).toHaveLength(requestsBefore);
    stop();
  });

  it("changes only the total when the same row arrives on page 2", async () => {
    const { api, stop } = await renderLive("/activity?page=2");
    await screen.findByRole("list", { name: "Journal events" });
    const firstBefore = feedRows()[0].textContent;
    const requestsBefore = api.requests.length;

    await appended(row(121, { task: "task-0200", kind: "gate", outcome: "fail" }));

    expect(screen.getByText("26-50 of 121 events")).toBeInTheDocument();
    expect(feedRows()).toHaveLength(25);
    expect(feedRows()[0].textContent).toBe(firstBefore);
    expect(screen.queryByText("task-0200")).not.toBeInTheDocument();
    expect(api.requests).toHaveLength(requestsBefore);
    stop();
  });

  it("changes nothing for a row the filters exclude", async () => {
    const { api, stop } = await renderLive("/activity?kind=approved");
    await screen.findByRole("list", { name: "Journal events" });
    const listBefore = feedRows().map((item) => item.textContent);
    const requestsBefore = api.requests.length;

    await appended(row(121, { task: "task-0200", kind: "gate", outcome: "fail" }));

    expect(feedRows().map((item) => item.textContent)).toEqual(listBefore);
    expect(screen.getByText("1-25 of 40 events")).toBeInTheDocument();
    expect(api.requests).toHaveLength(requestsBefore);
    stop();
  });

  it("prepends a row that matches the active filters", async () => {
    const { stop } = await renderLive("/activity?task=0200&kind=gate&result=fail", 120);
    expect(await screen.findByText("No events match.")).toBeInTheDocument();

    await appended(row(121, { task: "task-0200", kind: "gate", outcome: "fail" }));

    expect(feedRows()).toHaveLength(1);
    expect(screen.getByText("1-1 of 1 events")).toBeInTheDocument();
    stop();
  });
});

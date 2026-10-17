import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ROUTES } from "@/config/routes";
import { bytes } from "@/lib/format";
import lessons from "@/test/fixtures/memory/lessons.json";
import modules from "@/test/fixtures/memory/modules.json";
import patterns from "@/test/fixtures/memory/patterns.json";
import stats from "@/test/fixtures/memory/stats.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok, type ApiMock, type MockResponse } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";

import { MemoryPage } from "./MemoryPage";
import { validateMemorySearch } from "./search";

const FIXTURES = { lesson: lessons, pattern: patterns, module: modules } as const;
const PAGE = 20;

/** The list route answers with the fixture of the requested kind; `total` overrides meta.total. */
function listHandler(total?: number) {
  return (url: URL): MockResponse => {
    const kind = (url.searchParams.get("kind") ?? "lesson") as keyof typeof FIXTURES;
    const { data, meta } = FIXTURES[kind].body;
    return ok(data, { ...meta, total: total ?? meta.total, offset: Number(url.searchParams.get("offset") ?? 0) });
  };
}

function setup(total?: number): ApiMock {
  return mockApi({ "/api/v1/memory/stats": ok(stats.body.data), "/api/v1/memory": listHandler(total) });
}

function renderMemory(url = "/memory") {
  return renderWithApp(<MemoryPage />, { url, path: ROUTES.memory, validateSearch: validateMemorySearch });
}

const listParams = (api: ApiMock): URLSearchParams[] => api.callsTo("/api/v1/memory").map((call) => call.url.searchParams);

describe("Memory page states", () => {
  it("shows a loading status while the first request is pending", async () => {
    const api = setup();
    api.fetch.mockImplementation(() => new Promise<Response>(() => undefined));

    await renderMemory();

    expect(screen.getByRole("status", { name: "Loading memory" })).toBeInTheDocument();
  });

  it("explains an empty store", async () => {
    mockApi({ "/api/v1/memory/stats": ok(stats.body.data), "/api/v1/memory": ok([], { total: 0, limit: PAGE, offset: 0 }) });

    await renderMemory();

    expect(await screen.findByText("No memory rows yet.")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /pages|lessons/i })).not.toBeInTheDocument();
  });

  it("tells a search with no result apart from an empty store", async () => {
    mockApi({ "/api/v1/memory/stats": ok(stats.body.data), "/api/v1/memory": ok([], { total: 0, limit: PAGE, offset: 0 }) });

    await renderMemory("/memory?q=zzz");

    expect(await screen.findByText("Nothing matches.")).toBeInTheDocument();
  });

  it("shows the error with a retry that loads the rows", async () => {
    let calls = 0;
    mockApi({
      "/api/v1/memory/stats": ok(stats.body.data),
      "/api/v1/memory": (url) => (++calls === 1 ? fail(500, "internal", "Store exploded") : listHandler()(url)),
    });
    await renderMemory();

    expect(await screen.findByRole("alert")).toHaveTextContent("Store exploded");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("list", { name: "lessons" })).toBeInTheDocument();
  });

  it("shows the lessons and has no axe violation in the data state", async () => {
    setup();
    const { container } = await renderMemory();

    const list = await screen.findByRole("list", { name: "lessons" });

    expect(within(list).getAllByRole("listitem")).toHaveLength(lessons.body.data.length);
    await expectNoAxeViolations(container);
  });
});

describe("Memory tabs and URL state", () => {
  it("labels the first three tabs with the counts from the stats", async () => {
    setup();
    await renderMemory();

    expect(await screen.findByRole("tab", { name: "Lessons 45" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Patterns 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Modules 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Stats" })).toBeInTheDocument();
  });

  it("switches kind and the tab param, and resets the page", async () => {
    const api = setup(45);
    const { router } = await renderMemory("/memory?page=2");
    await screen.findByRole("list", { name: "lessons" });

    await userEvent.click(screen.getByRole("tab", { name: /Patterns/ }));

    expect(await screen.findByRole("list", { name: "patterns" })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ tab: "pattern" });
    const last = listParams(api).at(-1);
    expect(last?.get("kind")).toBe("pattern");
    expect(last?.get("offset")).toBe("0");
  });

  it("drops the filters that do not apply to the tab it switches to", async () => {
    setup();
    const { router } = await renderMemory("/memory?q=hook&agent=qa-engineer&area=hooks&task=task-0013&retired=only&page=2");
    await screen.findByRole("tab", { name: /Patterns/ });

    await userEvent.click(screen.getByRole("tab", { name: /Patterns/ }));
    expect(router.state.location.search).toEqual({ tab: "pattern", q: "hook", task: "task-0013" });

    await userEvent.click(screen.getByRole("tab", { name: /Modules/ }));
    expect(router.state.location.search).toEqual({ tab: "module", q: "hook" });
  });

  it("restores tab, search text and page from a reloaded URL", async () => {
    const api = setup(45);
    await renderMemory("/memory?tab=pattern&q=x&page=2");

    expect(await screen.findByRole("list", { name: "patterns" })).toBeInTheDocument();
    const params = listParams(api).at(-1);
    expect(params?.get("kind")).toBe("pattern");
    expect(params?.get("q")).toBe("x");
    expect(params?.get("offset")).toBe("20");
    expect(params?.get("limit")).toBe("20");
    expect(screen.getByRole("searchbox", { name: "Search memory" })).toHaveValue("x");
    expect(screen.getByRole("tab", { name: /Patterns/ })).toHaveAttribute("aria-selected", "true");
  });

  it("uses the shared pager: page 3 requests offset 40", async () => {
    const api = setup(45);
    const { router } = await renderMemory();
    expect(await screen.findByText("1-20 of 45 lessons")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Page 3" }));

    await waitFor(() => expect(listParams(api).at(-1)?.get("offset")).toBe("40"));
    expect(router.state.location.search).toEqual({ page: 3 });
    expect(await screen.findByText("41-45 of 45 lessons")).toBeInTheDocument();
  });

  it("corrects a page beyond the last page in the URL", async () => {
    setup(45);
    const { router } = await renderMemory("/memory?page=99");

    await waitFor(() => expect(router.state.location.search).toEqual({ page: 3 }));
  });
});

describe("Memory search and filters", () => {
  it("sends one request 300 ms after the last keystroke and keeps one history entry", async () => {
    const api = setup();
    const { router } = await renderMemory();
    const box = await screen.findByRole("searchbox", { name: "Search memory" });
    const before = listParams(api).length;
    const history = router.history.length;

    await userEvent.type(box, "hook");

    expect(listParams(api)).toHaveLength(before);
    await waitFor(() => expect(listParams(api)).toHaveLength(before + 1), { timeout: 1500 });
    expect(listParams(api).at(-1)?.get("q")).toBe("hook");
    expect(router.state.location.search).toEqual({ q: "hook" });
    expect(router.history.length).toBe(history);
  });

  it("sends the agent, area and retired filters and resets the page", async () => {
    const api = setup(45);
    const { router } = await renderMemory("/memory?page=2");
    await screen.findByRole("option", { name: "qa-engineer" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Agent" }), "qa-engineer");
    await waitFor(() => expect(listParams(api).at(-1)?.get("agent")).toBe("qa-engineer"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Area" }), "hooks");
    await waitFor(() => expect(listParams(api).at(-1)?.get("area")).toBe("hooks"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Retired lessons" }), "only");

    await waitFor(() => expect(listParams(api).at(-1)?.get("retired")).toBe("only"));
    expect(listParams(api).at(-1)?.get("offset")).toBe("0");
    expect(router.state.location.search).toEqual({ agent: "qa-engineer", area: "hooks", retired: "only" });
  });

  it("sends the task filter after the debounce", async () => {
    const api = setup();
    await renderMemory();

    await userEvent.type(await screen.findByRole("textbox", { name: "Source task" }), "task-0013");

    await waitFor(() => expect(listParams(api).at(-1)?.get("task")).toBe("task-0013"), { timeout: 1500 });
  });

  it("offers only the filters that apply to the tab", async () => {
    setup();
    await renderMemory("/memory?tab=module");
    await screen.findByRole("list", { name: "modules" });

    expect(screen.getByRole("searchbox", { name: "Search memory" })).toBeInTheDocument();
    for (const name of ["Agent", "Area", "Retired lessons"]) expect(screen.queryByRole("combobox", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Source task" })).not.toBeInTheDocument();
  });

  it("clears every filter and the search text with Clear filters", async () => {
    setup();
    const { router } = await renderMemory("/memory?q=hook&agent=qa-engineer&page=2");
    await screen.findByRole("list", { name: "lessons" });

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.getByRole("searchbox", { name: "Search memory" })).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });
});

describe("Memory cards", () => {
  it("shows every field of a lesson and links the source task", async () => {
    setup();
    await renderMemory();
    const [lesson] = lessons.body.data;
    const card = (await screen.findByRole("heading", { name: lesson.signature })).closest("li") as HTMLElement;

    for (const text of [lesson.trigger, lesson.what, lesson.why, lesson.fix, lesson.area, lesson.agent, "Recorded 2026-10-12"]) {
      expect(within(card).getByText(text)).toBeInTheDocument();
    }
    expect(within(card).getByRole("link", { name: lesson.task })).toHaveAttribute("href", `/tasks/${lesson.task}`);
    expect(within(card).queryByText("Retired")).not.toBeInTheDocument();
  });

  it("marks only the retired lesson with the badge", async () => {
    setup();
    await renderMemory();
    const retired = lessons.body.data.find((row) => row.retired);
    const card = (await screen.findByRole("heading", { name: retired?.signature })).closest("li") as HTMLElement;

    expect(within(card).getByText("Retired")).toBeInTheDocument();
    expect(screen.getAllByText("Retired")).toHaveLength(1);
  });

  it("wraps a 120-character signature and area and never ellipsizes or titles them", async () => {
    setup();
    await renderMemory();
    const long = lessons.body.data.find((row) => row.signature.length >= 120);
    const signature = await screen.findByRole("heading", { name: long?.signature });
    const area = screen.getByText(long?.area ?? "");

    for (const element of [signature, area]) {
      expect(element.className).toContain("wrap-anywhere");
      expect(element.className).not.toContain("truncate");
      expect(element.getAttribute("title")).not.toBe(element.textContent);
    }
  });

  it("shows the fields of a pattern card", async () => {
    setup();
    await renderMemory("/memory?tab=pattern");
    const [pattern] = patterns.body.data;
    const card = (await screen.findByRole("heading", { name: pattern.name })).closest("li") as HTMLElement;

    expect(within(card).getByText(pattern.use_when)).toBeInTheDocument();
    expect(within(card).getByLabelText("Body")).toHaveTextContent("sqlite3.connect");
    expect(within(card).getByRole("link", { name: pattern.task })).toBeInTheDocument();
    expect(within(card).getByText("Recorded 2026-10-12")).toBeInTheDocument();
  });

  it("shows the fields of a module card and hides empty ones", async () => {
    setup();
    await renderMemory("/memory?tab=module");
    const [full, bare] = modules.body.data;
    const card = (await screen.findByRole("heading", { name: full.path })).closest("li") as HTMLElement;
    const bareCard = screen.getByRole("heading", { name: bare.path }).closest("li") as HTMLElement;

    for (const text of [full.responsibility, full.symbols, full.notes, "Updated 2026-10-16"]) {
      expect(within(card).getByText(text)).toBeInTheDocument();
    }
    expect(within(bareCard).queryByText("Symbols")).not.toBeInTheDocument();
    expect(within(bareCard).queryByText("Notes")).not.toBeInTheDocument();
  });
});

describe("Memory Stats tab", () => {
  const data = stats.body.data;

  it("shows sizes, counts, growth, agent and area counts, stamps and the injection block", async () => {
    setup();
    await renderMemory("/memory?tab=stats");

    const panel = await screen.findByRole("tabpanel");
    const card = (label: string): HTMLElement => (within(panel).getByText(label).parentElement as HTMLElement);
    await within(panel).findByText("Database file");

    expect(card("Database file")).toHaveTextContent(bytes(data.db_bytes));
    expect(card("WAL file")).toHaveTextContent(bytes(data.wal_bytes));
    expect(card("Lessons")).toHaveTextContent(String(data.counts.lesson));
    expect(card("Patterns")).toHaveTextContent(String(data.counts.pattern));
    expect(card("Modules")).toHaveTextContent(String(data.counts.module));
    expect(within(panel).getByRole("img", { name: "Rows added per day over the last 4 days, 7 in total" })).toBeInTheDocument();
    for (const [name, count] of [...Object.entries(data.by_agent), ...Object.entries(data.by_area)]) {
      expect(within(panel).getByText(name).closest("li")).toHaveTextContent(String(count));
    }
    expect(within(panel).getByRole("link", { name: "task-0106" })).toBeInTheDocument();
    expect(within(panel).getByText("10-15 09:30:00Z")).toBeInTheDocument();
    expect(within(panel).getByText(bytes(data.injection.budget_bytes))).toBeInTheDocument();
    for (const task of data.injection.active_tasks) expect(within(panel).getByRole("link", { name: task })).toBeInTheDocument();
  });

  it("has one row per injection profile with matched, shown and bytes, and the would-be-injected note", async () => {
    setup();
    await renderMemory("/memory?tab=stats");

    const table = await screen.findByRole("table", { name: "Injection profiles" });
    const rows = within(table).getAllByRole("row").slice(1);

    expect(rows).toHaveLength(data.injection.profiles.length);
    data.injection.profiles.forEach((profile, index) => {
      const row = rows[index];
      expect(within(row).getByRole("rowheader")).toHaveTextContent(profile.matcher);
      const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
      expect(cells).toEqual([profile.kinds.join(", "), String(profile.limit), String(profile.matched), String(profile.shown), String(profile.bytes)]);
    });
    expect(screen.getByText(/would be injected now/)).toBeInTheDocument();
  });

  it("does not send a list request on the Stats tab", async () => {
    const api = setup();
    await renderMemory("/memory?tab=stats");
    await screen.findByText("Database file");

    expect(api.callsTo("/api/v1/memory")).toHaveLength(0);
  });

  it("shows a loading status, an empty store message and an error with retry", async () => {
    const pending = setup();
    pending.fetch.mockImplementation(() => new Promise<Response>(() => undefined));
    const loading = await renderMemory("/memory?tab=stats");
    expect(screen.getByRole("status", { name: "Loading memory" })).toBeInTheDocument();
    loading.unmount();

    const zero = { ...data, counts: { lesson: 0, pattern: 0, module: 0 }, db_bytes: 0, wal_bytes: 0 };
    mockApi({ "/api/v1/memory/stats": ok(zero) });
    const empty = await renderMemory("/memory?tab=stats");
    expect(await screen.findByText("No memory store yet.")).toBeInTheDocument();
    empty.unmount();

    mockApi({ "/api/v1/memory/stats": fail(500, "internal", "Stats failed") });
    await renderMemory("/memory?tab=stats");
    expect(await screen.findByRole("alert")).toHaveTextContent("Stats failed");
  });

  it("has no axe violation in the data state", async () => {
    setup();
    const { container } = await renderMemory("/memory?tab=stats");
    await screen.findByText("Database file");

    await expectNoAxeViolations(container);
  });
});

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { manyTasks } from "@/test/fixtures/pipeline/builders";

import { pagedHandler, renderApp, settle } from "./app";

const LONG_LIST = pagedHandler(manyTasks("done", 120, 1), {
  meta: () => ({ hidden_superseded: 0, facets: { folders: { backlog: 0, active: 0, done: 120 }, types: ["feature"] } }),
});

/*
 * QA: invalid URLs and search params through the REAL router (every feature route, production QueryClient).
 * TanStack merges validateSearch output over the raw params, so a validator that omits a key leaves a raw
 * invalid value in `search`; these cases fail if a raw value reaches the URL state or the API.
 */

/** Every param any page reads; unknown keys are the router's to keep, these are not. */
const KNOWN_PARAMS = ["folder", "type", "epic", "q", "superseded", "page", "task", "kind", "result", "tab", "agent", "area", "retired", "days", "view", "index"];

interface Case {
  name: string;
  url: string;
  /** The router's search state once the page has loaded (unknown keys are TanStack's to keep, so this is a subset check). */
  search: Record<string, unknown>;
  /** The route whose requests are checked. */
  api: string;
  /** The exact query string (sorted keys) of the first request to `api`. */
  query: string;
  /** Serve 120 tasks, so that page 2 exists. */
  routes?: boolean;
}

const CASES: Case[] = [
  {
    name: "tasks: bogus folder, page, flag, blank q, object epic, array type",
    url: "/tasks?folder=bogus&page=abc&superseded=0&q=%20&epic=%7B%22a%22%3A1%7D&type=%5B1%2C2%5D",
    search: {},
    api: "/api/v1/tasks",
    query: "limit=25&offset=0",
  },
  {
    name: "tasks: prototype-pollution keys are ignored",
    url: "/tasks?__proto__=x&constructor=y&folder=done",
    search: { folder: "done" },
    api: "/api/v1/tasks",
    query: "folder=done&limit=25&offset=0",
  },
  {
    name: "tasks: valid view is kept",
    url: "/tasks?folder=done&epic=epic-0005&q=login&superseded=1&type=feature&page=2",
    search: { folder: "done", epic: "epic-0005", q: "login", superseded: 1, type: "feature", page: 2 },
    api: "/api/v1/tasks",
    query: "epic=epic-0005&folder=done&include_superseded=true&limit=25&offset=25&q=login&type=feature",
    routes: true,
  },
  {
    name: "activity: script task, unknown kind, bad result, negative page",
    url: "/activity?task=%3Cscript%3E&kind=bogus&result=maybe&page=-3",
    search: {},
    api: "/api/v1/events",
    query: "limit=25&offset=0",
  },
  {
    name: "activity: task filter over 32 characters is dropped (the API would answer 422)",
    url: `/activity?task=${"a".repeat(33)}`,
    search: {},
    api: "/api/v1/events",
    query: "limit=25&offset=0",
  },
  {
    name: "activity: valid filters are kept",
    url: "/activity?task=TASK-0136&kind=gate&result=fail",
    search: { task: "TASK-0136", kind: "gate", result: "fail" },
    api: "/api/v1/events",
    query: "kind=gate&limit=25&offset=0&result=fail&task=TASK-0136",
  },
  {
    name: "memory: bogus tab, the default retired mode, page 0, array q",
    url: "/memory?tab=bogus&retired=exclude&page=0&q=%5B%5D",
    search: {},
    api: "/api/v1/memory",
    query: "kind=lesson&limit=20&offset=0",
  },
  {
    name: "memory: filters that do not apply to the Modules tab are dropped",
    url: "/memory?tab=module&agent=a&area=b&task=t&retired=only&q=x&page=2",
    search: { tab: "module", q: "x", page: 2 },
    api: "/api/v1/memory",
    query: "kind=module&limit=20&offset=20&q=x",
  },
  {
    name: "handoffs: fractional and text pages are page 1",
    url: "/handoffs?page=1.5",
    search: {},
    api: "/api/v1/handoffs",
    query: "limit=25&offset=0",
  },
  {
    name: "board: object epic and a superseded value other than 1",
    url: "/board?epic=%7B%7D&superseded=2",
    search: {},
    api: "/api/v1/tasks",
    query: "folder=backlog&limit=12",
  },
  {
    name: "metrics: ranges the control does not offer fall back to 14 days",
    url: "/metrics?days=91",
    search: {},
    api: "/api/v1/metrics",
    query: "days=14",
  },
  {
    name: "metrics: a non-numeric range falls back to 14 days",
    url: "/metrics?days=abc",
    search: {},
    api: "/api/v1/metrics",
    query: "days=14",
  },
  {
    name: "metrics: an offered range is kept",
    url: "/metrics?days=30",
    search: { days: 30 },
    api: "/api/v1/metrics",
    query: "days=30",
  },
  {
    name: "harness: tab names are case sensitive",
    url: "/harness?tab=HOOKS",
    search: {},
    api: "/api/v1/harness",
    query: "",
  },
];

describe("invalid URLs through the real router", () => {
  it.each(CASES)("$name", async ({ url, search, api, query, routes }) => {
    const app = await renderApp(url, routes ? { routes: { "/api/v1/tasks": LONG_LIST } } : {});
    await settle(150);

    const state = app.router.state.location.search as Record<string, unknown>;
    for (const key of KNOWN_PARAMS) expect(state[key], `search.${key}`).toEqual(search[key]);
    const first = app.api.callsTo(api)[0];
    expect(first, `no request to ${api}`).toBeDefined();
    expect(first.url.searchParams.toString()).toBe(query);
  });

  it("memory on the Stats tab sends no list request and drops every list filter", async () => {
    const app = await renderApp("/memory?tab=stats&q=x&agent=y&page=3");
    await settle(150);

    expect(app.router.state.location.search).toEqual({ tab: "stats" });
    expect(app.api.callsTo("/api/v1/memory")).toHaveLength(0);
    expect(app.api.callsTo("/api/v1/memory/stats").length).toBeGreaterThan(0);
  });

  it("graphs: an unknown view and a stray index are dropped", async () => {
    const app = await renderApp("/graphs?view=bogus&index=x");
    await settle(150);

    expect(app.router.state.location.search).toEqual({});
    expect(app.api.callsTo("/api/v1/graphs/memory")).toHaveLength(1);
  });

  it("graphs: an unknown code index falls back to the first supported index", async () => {
    const app = await renderApp("/graphs?view=code&index=does-not-exist");
    await settle(200);

    expect(app.api.callsTo("/api/v1/codegraph").length).toBeGreaterThan(0);
    const graphRequests = app.api.requests.filter((request) => request.url.pathname.startsWith("/api/v1/codegraph/"));
    expect(graphRequests.every((request) => !request.url.pathname.includes("does-not-exist"))).toBe(true);
  });
});

describe("paths the router does not know", () => {
  it.each(["/nope", "/tasks/task-0104/extra", "/handoffs/a/b", "/tasks/task-0104/", "/TASKS", "/tasks//", "/%2e%2e/etc/passwd", "/?x=1#frag"])(
    "%s keeps the shell and shows a page",
    async (path) => {
      await renderApp(path);
      await settle(150);

      expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
      expect(screen.getAllByRole("heading", { level: 1 }).length).toBeGreaterThan(0);
    },
  );

  it("a path with an encoded slash keeps the shell", async () => {
    await renderApp("/tasks/task%2F0104");
    await settle(150);

    expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
  });
});

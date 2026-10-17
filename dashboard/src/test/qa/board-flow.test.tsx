import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { manyTasks, summary } from "@/test/fixtures/pipeline/builders";
import type { Folder, TaskSummary } from "@/types/api";

import { ok, qaUser, renderApp, settle, until } from "./app";

/*
 * QA: the Board's cap boundary (12 and 13), the View all link with every filter combination, and the trip
 * Board -> View all -> Tasks, where the count in the link text must equal the total the Tasks page shows (FR-125).
 */

function server(sizes: Record<Folder, number>, superseded = 0) {
  const all: TaskSummary[] = [
    ...manyTasks("backlog", sizes.backlog, 1, { epic: "epic-0005" }),
    ...manyTasks("active", sizes.active, 100, { epic: "epic-0005" }),
    ...manyTasks("done", sizes.done, 200, { epic: "epic-0005" }),
    ...Array.from({ length: superseded }, (_, index) => summary(`task-09${String(index).padStart(2, "0")}`, "backlog", { superseded_by: "task-0001", epic: "epic-0005" })),
  ];
  return (url: URL) => {
    const get = (name: string) => url.searchParams.get(name);
    const folderRows = (folder: string | null) => all.filter((task) => !folder || task.folder === folder);
    const visible = (task: TaskSummary) => get("include_superseded") === "true" || !task.superseded_by;
    const matching = folderRows(get("folder")).filter((task) => (!get("epic") || task.epic === get("epic")));
    const shown = matching.filter(visible);
    const limit = Number(get("limit") ?? 25);
    const offset = Number(get("offset") ?? 0);
    const count = (folder: Folder) => all.filter((task) => task.folder === folder && visible(task) && (!get("epic") || task.epic === get("epic"))).length;
    return ok(shown.slice(offset, offset + limit), {
      total: shown.length,
      limit,
      offset,
      hidden_superseded: get("include_superseded") === "true" ? 0 : matching.length - shown.length,
      facets: { folders: { backlog: count("backlog"), active: count("active"), done: count("done") }, types: ["feature"] },
    });
  };
}

const column = (name: string) => screen.getByRole("region", { name });

describe("Board cap boundary", () => {
  it("a column with exactly 12 tasks has no footer and no link; one with 13 has both", async () => {
    await renderApp("/board", {
      routes: {
        "/api/v1/tasks": server({ backlog: 12, active: 13, done: 0 }),
        "/api/v1/epics": ok([]),
      },
    });
    await settle(200);

    expect(within(column("Backlog")).queryByText(/Showing/)).toBeNull();
    expect(within(column("Backlog")).queryByRole("link", { name: /View all/ })).toBeNull();
    expect(within(column("Backlog")).getAllByRole("article")).toHaveLength(12);
    expect(within(column("Active")).getByText("Showing 12 of 13")).toBeInTheDocument();
    expect(within(column("Active")).getByRole("link", { name: "View all (13)" })).toHaveAttribute("href", "/tasks?folder=active");
    expect(within(column("Active")).getAllByRole("article")).toHaveLength(12);
    expect(within(column("Done")).queryByRole("link", { name: /View all/ })).toBeNull();
  });

  it("an empty column and a one-card column show no footer", async () => {
    await renderApp("/board", { routes: { "/api/v1/tasks": server({ backlog: 0, active: 1, done: 0 }), "/api/v1/epics": ok([]) } });
    await settle(200);

    expect(screen.queryByText(/Showing/)).toBeNull();
    expect(within(column("Active")).getAllByRole("article")).toHaveLength(1);
  });

  it("sends exactly three column requests of 12 and nothing else for the cards", async () => {
    const app = await renderApp("/board", { routes: { "/api/v1/tasks": server({ backlog: 30, active: 30, done: 30 }), "/api/v1/epics": ok([]) } });
    await settle(200);

    const queries = app.api.callsTo("/api/v1/tasks").map((request) => request.url.searchParams.toString()).sort();
    expect(queries).toEqual(["folder=active&limit=12", "folder=backlog&limit=12", "folder=done&limit=12"]);
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });
});

describe("View all keeps the Board filters and lands on a list of the same size", () => {
  it("epic and superseded are carried, and the Tasks page shows the number in the link", async () => {
    const user = qaUser();
    const app = await renderApp("/board?epic=epic-0005&superseded=1", {
      routes: {
        "/api/v1/tasks": server({ backlog: 5, active: 2, done: 30 }, 4),
        "/api/v1/epics": ok([{ id: "epic-0005", title: "Dashboard", status: "active", tasks: [] }]),
      },
    });
    await settle(200);

    const link = within(column("Done")).getByRole("link", { name: "View all (30)" });
    expect(link).toHaveAttribute("href", "/tasks?folder=done&epic=epic-0005&superseded=1");
    await user.click(link);
    await until(() => expect(screen.getByText("1-25 of 30 tasks")).toBeInTheDocument());

    expect(app.router.state.location.pathname).toBe("/tasks");
    expect(app.router.state.location.search).toEqual({ folder: "done", epic: "epic-0005", superseded: 1 });
  });

  it("the superseded note counts the hidden tasks of the three columns and the toggle re-requests all three", async () => {
    const user = qaUser();
    const app = await renderApp("/board", { routes: { "/api/v1/tasks": server({ backlog: 3, active: 0, done: 0 }, 2), "/api/v1/epics": ok([]) } });
    await settle(200);
    expect(screen.getByText("2 superseded hidden")).toBeInTheDocument();
    const before = app.api.callsTo("/api/v1/tasks").length;

    await user.click(screen.getByRole("checkbox", { name: /superseded/i }));
    await settle(250);

    const added = app.api.callsTo("/api/v1/tasks").slice(before);
    expect(added.map((request) => request.url.searchParams.get("include_superseded"))).toEqual(["true", "true", "true"]);
    expect(app.router.state.location.search).toEqual({ superseded: 1 });
  });
});

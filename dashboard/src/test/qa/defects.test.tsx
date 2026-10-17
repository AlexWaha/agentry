import { act, render, screen } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { StatusChip } from "@/components/shared/StatusChip";
import { renderMarkdown, safeLink } from "@/lib/markdown";
import { manyTasks } from "@/test/fixtures/pipeline/builders";
import detailFixture from "@/test/fixtures/pipeline/task-detail.json";
import runsFixture from "@/test/fixtures/shell/runs.json";
import { FakeWebSocket, helloMessage } from "@/test/utils/fake-ws";
import type { Event } from "@/types/api";

import { fail, ok, pagedHandler, qaUser, renderApp, settle } from "./app";
import { findOverflowRisks, LONG_ROUTES, longValue } from "./long-values";

/*
 * QA DEFECTS. Every test here states the behaviour the spec or the User's decisions require and FAILS today; the
 * name starts with the finding id of .agentry/tasks/swarm/task-0149/qa-frontend.md. When a fix lands the test
 * turns green and stays as a regression test. Nothing in this file may be weakened to pass.
 */

const TASK_META = () => ({ hidden_superseded: 0, facets: { folders: { backlog: 0, active: 0, done: 120 }, types: ["feature"] } });
const TASKS = pagedHandler(manyTasks("done", 120, 1), { meta: TASK_META });

/* ---------- navigation and error pages ---------- */

describe("QA-F01 a malformed URL does not take the shell down", () => {
  it.each(["/tasks/%E0%A4%A", "/handoffs/%", "/%"])("%s keeps the sidebar and shows a page", async (path) => {
    await renderApp(path);
    await settle(150);

    expect(screen.queryByText("Something went wrong while loading this page.")).toBeNull();
    expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
  });
});

describe("QA-F02 an id the API refuses is an unknown id (FR-97)", () => {
  const invalid = fail(422, "validation_failed", "Invalid request", { id: ["must be task-<digits>"] });

  it("a task id that is not task-NNNN shows the task not-found page, not a Retry button", async () => {
    await renderApp("/tasks/not-a-task", { routes: { "/api/v1/tasks/not-a-task": invalid } });
    await settle(150);

    expect(screen.getByRole("heading", { name: "Task not found" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("a handoff id that is not task-NNNN shows the handoff not-found page, not a Retry button", async () => {
    await renderApp("/handoffs/not-a-task", { routes: { "/api/v1/handoffs/not-a-task": invalid } });
    await settle(150);

    expect(screen.getByRole("heading", { name: "Handoff not found" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

/* ---------- URL validation and filters ---------- */

describe("QA-F03 Tasks drops a type the API would refuse (FR-12, FR-124)", () => {
  it("?type=has space is not sent and is removed from the URL", async () => {
    const app = await renderApp("/tasks?type=has%20space", { routes: { "/api/v1/tasks": TASKS } });
    await settle(150);

    expect(app.api.callsTo("/api/v1/tasks").every((request) => !request.url.searchParams.has("type"))).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(app.router.state.location.search).not.toHaveProperty("type");
  });
});

describe("QA-F04 the Board epic select shows the epic the URL filters by", () => {
  it("?epic=unknown-epic keeps its value visible, as the Tasks page does", async () => {
    await renderApp("/board?epic=unknown-epic");
    await settle(150);

    expect(screen.getByRole("combobox", { name: "Filter by epic" })).toHaveValue("unknown-epic");
  });
});

describe("QA-F05 Clear filters clears the search text that is still pending", () => {
  it("typing and pressing Clear within the debounce leaves no filter behind", async () => {
    const user = qaUser();
    const app = await renderApp("/tasks?folder=done", { routes: { "/api/v1/tasks": TASKS } });
    await settle(150);
    const box = screen.getByRole("searchbox", { name: "Search tasks by id or title" });

    await user.type(box, "abc");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await settle(700);

    expect(app.router.state.location.search).toEqual({});
    expect(box).toHaveValue("");
  });
});

describe("QA-F06 a search text is sent as typed", () => {
  it("?q=1.50 is not turned into 1.5 by the JSON search parser", async () => {
    const app = await renderApp("/tasks?q=1.50", { routes: { "/api/v1/tasks": TASKS } });
    await settle(150);

    expect(app.api.callsTo("/api/v1/tasks")[0].url.searchParams.get("q")).toBe("1.50");
  });
});

describe("QA-F07 a page number that does not fit in a request is page 1 or the last page", () => {
  it("?page=99999999999999999999 never sends a non-integer offset", async () => {
    const app = await renderApp("/tasks?page=99999999999999999999", { routes: { "/api/v1/tasks": TASKS } });
    await settle(200);

    expect(app.api.callsTo("/api/v1/tasks").every((request) => /^\d+$/.test(request.url.searchParams.get("offset") ?? ""))).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

/* ---------- live updates ---------- */

describe("QA-F08 the Overview latest events follow the journal (FR-99)", () => {
  it("an events.appended row appears in the Latest events list while the page is open", async () => {
    await renderApp("/", { ws: true });
    await settle(150);
    act(() => {
      FakeWebSocket.last.open();
      FakeWebSocket.last.receive(helloMessage);
    });
    await settle(150);
    const row: Event = {
      id: 9999,
      at: "2026-10-16T10:00:00Z",
      lane: "default",
      task: "task-7777",
      pipeline: "build",
      kind: "merged",
      stage: "done",
      from_stage: "",
      outcome: "",
      via: "",
      detail: {},
    };

    act(() => {
      FakeWebSocket.last.receive({ type: "events.appended", v: 1, payload: { events: [row] } });
    });
    await settle(700);

    expect(screen.getByRole("list", { name: "Latest events" })).toHaveTextContent("task-7777");
  });
});

describe("QA-F09 a row delivered twice is shown once", () => {
  it("the second events.appended with the same id changes nothing", async () => {
    await renderApp("/activity", { ws: true });
    await settle(150);
    act(() => {
      FakeWebSocket.last.open();
      FakeWebSocket.last.receive(helloMessage);
    });
    await settle(100);
    const message = {
      type: "events.appended",
      v: 1,
      payload: { events: [{ id: 777, at: "2026-10-16T10:00:00Z", lane: "", task: "task-0136", pipeline: "build", kind: "gate", stage: "review", from_stage: "", outcome: "pass", via: "", detail: {} }] },
    };

    act(() => {
      FakeWebSocket.last.receive(message);
      FakeWebSocket.last.receive(message);
    });
    await settle(100);

    const rows = screen.getByRole("list", { name: "Journal events" }).querySelectorAll("li");
    expect([...rows].filter((row) => row.textContent?.includes("10:00:00Z"))).toHaveLength(1);
  });
});

/* ---------- rendering ---------- */

describe("QA-F10 two sections with the same title render with unique keys", () => {
  it("a task body with two '## Notes' sections raises no duplicate-key warning", async () => {
    const warnings: string[] = [];
    vi.spyOn(console, "error").mockImplementation((...args) => {
      warnings.push(args.map(String).join(" "));
    });
    await renderApp("/tasks/task-0104", {
      routes: { "/api/v1/tasks/task-0104": ok({ ...detailFixture.body.data, body: "## Notes\n\nfirst\n\n## Notes\n\nsecond\n" }) },
    });
    await settle(200);

    expect(warnings.filter((line) => line.includes("same key"))).toEqual([]);
    expect(screen.getAllByRole("heading", { name: "Notes" })).toHaveLength(2);
  });
});

describe("QA-F11 a link target is classified the way the browser reads it", () => {
  it("https:evil.example leaves the app (the dashboard itself runs on http), so it is external", () => {
    expect(safeLink("https:evil.example")?.external).toBe(true);
  });
});

describe("QA-F12 markdown inline parsing is linear in the text", () => {
  it("doubling an unclosed-bracket paragraph less than triples the time", () => {
    const time = (repeat: number): number => {
      const start = performance.now();
      renderMarkdown(["[a ".repeat(repeat) + "]"]);
      return performance.now() - start;
    };
    time(2000);

    const small = time(16000);
    const large = time(32000);

    expect(large / Math.max(small, 1)).toBeLessThan(3);
  });
});

describe("QA-F13 long values in the Harness, Handoffs and markdown wrap (FR-128, FR-90)", () => {
  it.each(["/harness", "/harness?tab=skills", "/handoffs", "/handoffs/task-0107"])("%s", async (page) => {
    await renderApp(page, { routes: LONG_ROUTES });
    await settle(250);

    expect(findOverflowRisks()).toEqual([]);
  });

  it("/tasks/task-0104: paragraphs, list items, headings, quotes and links of a task body wrap", async () => {
    const long = longValue("path");
    const body = [
      "## Description",
      "",
      `paragraph ${long} tail`,
      "",
      `- list item ${long}`,
      "",
      `### heading ${long}`,
      "",
      `> quote ${long}`,
      "",
      `[${long}](https://example.com/${long})`,
    ].join("\n");
    await renderApp("/tasks/task-0104", { routes: { "/api/v1/tasks/task-0104": ok({ ...detailFixture.body.data, body }) } });
    await settle(250);

    expect(findOverflowRisks()).toEqual([]);
  });
});

describe("QA-F14 an ellipsized status label carries its full text (FR-128)", () => {
  it("StatusChip with a long label has a title", () => {
    const label = longValue("label");
    render(<StatusChip status="need" label={label} />);

    expect(document.querySelector("[data-status]")?.getAttribute("title") ?? document.querySelector("[data-status] .truncate")?.getAttribute("title")).toBe(label);
  });
});

describe("QA-F15 orange is for Needs you only (FR-85, User decision)", () => {
  it("a run that waits for nobody does not ring its current human step in orange", async () => {
    const run = {
      ...runsFixture.body.data[0],
      awaiting_human: "",
      needs_you: null,
      stage_status: "ok",
      stages: runsFixture.body.data[0].stages.map((stage) => ({ ...stage, state: stage.name === "plan-approval" ? "now" : stage.state === "now" ? "next" : stage.state })),
    };
    await renderApp("/", { routes: { "/api/v1/runs": ok([run]) } });
    await settle(250);

    const current = document.querySelector("li[data-state=now]") as HTMLElement;
    expect(current.innerHTML).not.toContain("text-attention");
    expect(current.innerHTML).not.toContain("border-attention");
  });
});

/* ---------- performance, catalog, contrast ---------- */

describe("QA-F16 the Graphs page stays usable on a memory store of NFR-14 size", () => {
  it("draws a 2000-node memory graph in under 1.5 seconds", async () => {
    const nodes = Array.from({ length: 2000 }, (_, index) => ({ id: `n${index}`, kind: "lesson", label: `n${index}`, weight: 1 }));
    const edges = Array.from({ length: 4000 }, (_, index) => ({ source: `n${index % 2000}`, target: `n${(index * 7 + 1) % 2000}`, kind: "x", weight: 1 }));
    const start = performance.now();

    await renderApp("/graphs", { routes: { "/api/v1/graphs/memory": ok({ nodes, edges }) } });
    await settle(0);
    expect(screen.getByRole("application", { name: /Memory graph, 400 nodes/ })).toBeInTheDocument();

    expect(performance.now() - start).toBeLessThan(1500);
  });
});

describe("QA-F17 the message catalog holds no dead key", () => {
  it("every key of i18n/messages is used by the shipped code, literally or by a prefix it builds", () => {
    const src = path.resolve(import.meta.dirname, "../..");
    const code = readdirSync(src, { recursive: true, encoding: "utf8" })
      .map((entry) => entry.replaceAll("\\", "/"))
      .filter((file) => /\.(ts|tsx)$/.test(file) && !/\.test\.tsx?$/.test(file) && !file.startsWith("test/") && !file.startsWith("i18n/messages/"))
      .map((file) => readFileSync(path.join(src, file), "utf8"))
      .join("\n");
    const groups = import.meta.glob<Record<string, string>>("../../i18n/messages/*.ts", { eager: true, import: "default" });
    const catalog = Object.assign({}, ...Object.values(groups)) as Record<string, string>;
    const prefixes = [...code.matchAll(/`([a-z][A-Za-z.]*\.)\$\{/g)].map((match) => match[1]);

    const unused = Object.keys(catalog).filter((key) => !code.includes(`"${key}"`) && !prefixes.some((prefix) => key.startsWith(prefix)));

    expect(unused).toEqual([]);
  });
});

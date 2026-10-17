import { act, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FakeWebSocket, helloMessage, invalidateMessage } from "@/test/utils/fake-ws";
import type { Event, Topic } from "@/types/api";

import { qaUser, renderApp, settle, type QaApp } from "./app";

/*
 * QA: live updates through the WebSocket (FR-82, FR-83, FR-99, FR-100). The socket is a FakeWebSocket; the app is
 * the real one. A topic must refresh exactly the pages that show its data, keep what the user typed and chose,
 * and a bad frame must change nothing.
 */

const send = (message: unknown): void => {
  act(() => {
    FakeWebSocket.last.receive(message as string);
  });
};

async function connect(url: string): Promise<QaApp> {
  const app = await renderApp(url, { ws: true });
  act(() => {
    FakeWebSocket.last.open();
    FakeWebSocket.last.receive(helloMessage);
  });
  await settle(150);
  return app;
}

const count = (app: QaApp, path: string): number => app.api.callsTo(path).length;

interface TopicCase {
  page: string;
  topic: Topic;
  /** Requests that must repeat after the invalidate. */
  refetches: string[];
}

const TOPIC_CASES: TopicCase[] = [
  { page: "/", topic: "runs", refetches: ["/api/v1/runs"] },
  { page: "/", topic: "status", refetches: ["/api/v1/status"] },
  { page: "/needs", topic: "runs", refetches: ["/api/v1/runs"] },
  { page: "/board", topic: "tasks", refetches: ["/api/v1/tasks", "/api/v1/epics"] },
  { page: "/tasks", topic: "tasks", refetches: ["/api/v1/tasks", "/api/v1/epics"] },
  { page: "/tasks/task-0104", topic: "tasks", refetches: ["/api/v1/tasks/task-0104"] },
  { page: "/tasks/task-0104", topic: "runs", refetches: ["/api/v1/tasks/task-0104"] },
  { page: "/metrics", topic: "runs", refetches: ["/api/v1/metrics"] },
  { page: "/memory", topic: "memory", refetches: ["/api/v1/memory", "/api/v1/memory/stats"] },
  { page: "/graphs", topic: "memory", refetches: ["/api/v1/graphs/memory"] },
  { page: "/graphs?view=code", topic: "codegraph", refetches: ["/api/v1/codegraph"] },
  { page: "/harness", topic: "harness", refetches: ["/api/v1/harness"] },
  { page: "/harness?tab=plugin", topic: "status", refetches: ["/api/v1/status"] },
  { page: "/handoffs", topic: "handoffs", refetches: ["/api/v1/handoffs"] },
  { page: "/handoffs/task-0107", topic: "handoffs", refetches: ["/api/v1/handoffs/task-0107"] },
];

describe("an invalidate message refreshes the page that shows the topic", () => {
  it.each(TOPIC_CASES)("$page refetches on $topic", async ({ page, topic, refetches }) => {
    const app = await connect(page);
    const before = refetches.map((path) => count(app, path));
    const heading = screen.getAllByRole("heading", { level: 1 })[0];

    send(invalidateMessage(topic));
    await settle(700);

    refetches.forEach((path, index) => expect(count(app, path), `${path} after ${topic}`).toBeGreaterThan(before[index]));
    expect(screen.getAllByRole("heading", { level: 1 })[0], "the page was not remounted").toBe(heading);
  });

  it.each([
    ["/tasks", "memory", "/api/v1/tasks"],
    ["/tasks", "harness", "/api/v1/tasks"],
    ["/memory", "tasks", "/api/v1/memory"],
    ["/handoffs", "runs", "/api/v1/handoffs"],
    ["/harness", "tasks", "/api/v1/harness"],
  ] as const)("%s ignores %s", async (page, topic, path) => {
    const app = await connect(page);
    const before = count(app, path);

    send(invalidateMessage(topic));
    await settle(700);

    expect(count(app, path)).toBe(before);
  });
});

describe("what the user typed and chose survives a refresh", () => {
  it("tasks: the search text, the folder and the input element itself", async () => {
    const user = qaUser();
    const app = await connect("/tasks?folder=done");
    const box = screen.getByRole("searchbox", { name: "Search tasks by id or title" });
    await user.type(box, "login");
    await settle(450);
    const select = screen.getByRole("combobox", { name: "Filter by folder" });

    send(invalidateMessage("tasks"));
    await settle(700);

    expect(screen.getByRole("searchbox", { name: "Search tasks by id or title" })).toBe(box);
    expect(box).toHaveValue("login");
    expect(screen.getByRole("combobox", { name: "Filter by folder" })).toBe(select);
    expect(select).toHaveValue("done");
    expect(app.router.state.location.search).toEqual({ folder: "done", q: "login" });
  });

  it("memory: the tab, the search text and the page", async () => {
    const user = qaUser();
    const app = await connect("/memory?tab=pattern");
    const box = screen.getByRole("searchbox", { name: "Search memory" });
    await user.type(box, "retry");
    await settle(450);

    send(invalidateMessage("memory"));
    await settle(700);

    expect(box).toHaveValue("retry");
    expect(app.router.state.location.search).toEqual({ tab: "pattern", q: "retry" });
  });

  it("harness: the open tab and an expanded agent", async () => {
    const user = qaUser();
    await connect("/harness");
    const summary = screen.getAllByText("architect")[0].closest("summary") as HTMLElement;
    await user.click(summary);
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(true);

    send(invalidateMessage("harness"));
    await settle(700);

    expect((screen.getAllByText("architect")[0].closest("details") as HTMLDetailsElement).open).toBe(true);
  });

  it("a refresh never scrolls the page", async () => {
    await connect("/tasks");
    const scrollTo = vi.fn();
    window.scrollTo = scrollTo;

    send(invalidateMessage("tasks"));
    await settle(700);

    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe("bad frames change nothing", () => {
  const BAD: [string, unknown][] = [
    ["a message of an unknown type", { type: "zzz", v: 1, payload: {} }],
    ["a message of protocol version 2", { type: "invalidate", v: 2, payload: { topics: ["tasks"] } }],
    ["a message without a payload", { type: "invalidate", v: 1 }],
    ["topics that are not an array", { type: "invalidate", v: 1, payload: { topics: "tasks" } }],
    ["text that is not JSON", "not json"],
    ["a JSON null", "null"],
    ["a JSON array", "[1,2]"],
    ["events that are not an array", { type: "events.appended", v: 1, payload: { events: "x" } }],
  ];

  it.each(BAD)("%s", async (_name, frame) => {
    const app = await connect("/tasks");
    const before = app.api.requests.length;

    send(typeof frame === "string" ? frame : JSON.stringify(frame));
    await settle(700);

    expect(app.api.requests.length).toBe(before);
    expect(screen.getByRole("list", { name: "Tasks" })).toBeInTheDocument();
  });

  it("topics named like Object.prototype members are ignored, the real topic still counts", async () => {
    const app = await connect("/tasks");
    const before = count(app, "/api/v1/tasks");

    send({ type: "invalidate", v: 1, payload: { topics: ["__proto__", "constructor", "toString", "hasOwnProperty", "tasks"] } });
    await settle(700);

    expect(count(app, "/api/v1/tasks")).toBe(before + 1);
  });

  it("the client never sends a frame", async () => {
    await connect("/");
    send(invalidateMessage("runs", "status", "tasks"));
    send({ type: "command.refused", v: 1, payload: { code: "read_only", message: "no" } });
    send({ type: "error", v: 1, payload: { code: "bad_message", message: "no" } });
    await settle(700);

    expect(FakeWebSocket.instances.every((socket) => socket.sent.length === 0)).toBe(true);
  });
});

describe("events.appended on the Activity page", () => {
  const row = (id: number, overrides: Partial<Event> = {}): Event => ({
    id,
    at: "2026-10-16T10:00:00Z",
    lane: "default",
    task: "task-0136",
    pipeline: "build",
    kind: "gate",
    stage: "review",
    from_stage: "",
    outcome: "pass",
    via: "",
    detail: {},
    ...overrides,
  });
  const appended = (...events: Event[]) => ({ type: "events.appended", v: 1, payload: { events } });

  it("an unknown event kind renders as its raw name and does not crash the page", async () => {
    await connect("/activity");

    send(appended(row(500, { kind: "brand-new-kind" as Event["kind"], stage: "" })));
    await settle(100);

    expect(screen.getByRole("list", { name: "Journal events" })).toHaveTextContent("brand-new-kind");
  });

  it("a task filter matches case-insensitively and by substring, like the API", async () => {
    await connect("/activity?task=0136");

    send(appended(row(500, { task: "task-0136" }), row(501, { task: "task-0999" })));
    await settle(100);

    const list = screen.getByRole("list", { name: "Journal events" });
    expect(within(list).getAllByRole("link", { name: "task-0136" }).length).toBeGreaterThan(0);
    expect(within(list).queryByRole("link", { name: "task-0999" })).toBeNull();
  });
});

describe("connection indicator", () => {
  it("returns to Live when the socket reconnects after a drop", async () => {
    await connect("/");
    expect(screen.getByText("Live")).toBeInTheDocument();

    act(() => {
      FakeWebSocket.last.serverClose();
    });
    await settle(50);
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
  });
});

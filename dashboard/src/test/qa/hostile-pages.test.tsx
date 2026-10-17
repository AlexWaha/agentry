import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fromFixture } from "@/test/fixtures/pipeline/builders";
import activityEvents from "@/test/fixtures/activity/events.json";
import metricsFixture from "@/test/fixtures/activity/metrics.json";
import handoffFixture from "@/test/fixtures/harness/handoff.json";
import handoffsFixture from "@/test/fixtures/harness/handoffs.json";
import harnessFixture from "@/test/fixtures/harness/harness.json";
import codegraphFixture from "@/test/fixtures/memory/codegraph.json";
import lessonsFixture from "@/test/fixtures/memory/lessons.json";
import memoryGraphFixture from "@/test/fixtures/memory/graph-memory.json";
import modulesFixture from "@/test/fixtures/memory/modules.json";
import patternsFixture from "@/test/fixtures/memory/patterns.json";
import memoryStatsFixture from "@/test/fixtures/memory/stats.json";
import epicsFixture from "@/test/fixtures/pipeline/epics.json";
import eventsFixture from "@/test/fixtures/pipeline/events.json";
import taskDetailFixture from "@/test/fixtures/pipeline/task-detail.json";
import tasksFixture from "@/test/fixtures/pipeline/tasks-list.json";
import runsFixture from "@/test/fixtures/shell/runs.json";
import statusFixture from "@/test/fixtures/shell/status.json";
import { ok, type MockHandler } from "@/test/utils/api-mock";

import { renderApp, settle } from "./app";

/*
 * QA: every free-text field the API can send, turned hostile, rendered by its page. A string from the store
 * (a title, a reason, a signature, a hook command) is untrusted: it must show as text and never as markup.
 */

const PAYLOAD = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';

/** Fields the UI switches on; the server only sends the documented values for these. */
const ENUM_KEYS = new Set(["folder", "state", "delivery", "install", "pipeline", "mode", "approvals", "kind", "profile", "level", "protocol", "status"]);

function hostile(value: unknown, key = ""): unknown {
  if (typeof value === "string") return ENUM_KEYS.has(key) || /^\d{4}-\d{2}-\d{2}/.test(value) ? value : `${PAYLOAD}${value}`;
  if (Array.isArray(value)) return value.map((item) => hostile(item, key));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, hostile(item, name)]));
  }
  return value;
}

const serve = (fixture: { body: { data: unknown; meta?: unknown } }): MockHandler => {
  const body = hostile(fixture.body) as { data: unknown; meta?: unknown };
  return ok(body.data, body.meta);
};

const ROUTES: Record<string, MockHandler> = {
  "/api/v1/status": serve(statusFixture),
  "/api/v1/runs": serve(runsFixture),
  "/api/v1/tasks": serve(tasksFixture),
  "/api/v1/tasks/task-0104": serve(taskDetailFixture),
  "/api/v1/epics": serve(epicsFixture),
  "/api/v1/events": (url) => (url.searchParams.get("limit") === "5" ? serve(eventsFixture) : serve(activityEvents)) as never,
  "/api/v1/metrics": serve(metricsFixture),
  "/api/v1/memory": (url) => {
    const kind = url.searchParams.get("kind");
    return (kind === "pattern" ? serve(patternsFixture) : kind === "module" ? serve(modulesFixture) : serve(lessonsFixture)) as never;
  },
  "/api/v1/memory/stats": serve(memoryStatsFixture),
  "/api/v1/graphs/memory": serve(memoryGraphFixture),
  "/api/v1/codegraph": serve(codegraphFixture),
  "/api/v1/harness": serve(harnessFixture),
  "/api/v1/handoffs": serve(handoffsFixture),
  "/api/v1/handoffs/task-0107": serve(handoffFixture),
};

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
  "/harness",
  "/harness?tab=rules",
  "/harness?tab=skills",
  "/harness?tab=hooks",
  "/harness?tab=plugin",
  "/handoffs",
  "/handoffs/task-0107",
];

describe("hostile text from the API renders as text on every page", () => {
  it.each(PAGES)("%s", async (page) => {
    await renderApp(page, { routes: ROUTES });
    await settle(250);

    expect(document.body.querySelectorAll("script, img, iframe, object, embed, style, meta, base, form, svg[onload]")).toHaveLength(0);
    for (const element of document.body.querySelectorAll("*")) {
      for (const attribute of element.getAttributeNames()) expect(attribute.startsWith("on"), `${element.tagName} ${attribute}`).toBe(false);
    }
    // The graph draws a label only on focus; its table view is checked below.
    if (page !== "/graphs") expect(screen.getByRole("main")).toHaveTextContent("alert(");
  });

  it("the hostile text shows up in the memory graph labels and in its table view", async () => {
    await renderApp("/graphs", { routes: ROUTES });
    await settle(250);
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    await settle(50);

    expect(document.body.querySelectorAll("script, img, iframe, svg[onload]")).toHaveLength(0);
    expect(screen.getByRole("main")).toHaveTextContent("alert(");
  });

  it("the hostile text shows up in the metrics tables", async () => {
    await renderApp("/metrics", { routes: ROUTES });
    await settle(250);
    for (const button of screen.getAllByRole("button", { name: "Show as table" })) fireEvent.click(button);
    await settle(50);

    expect(document.body.querySelectorAll("script, img, iframe, svg[onload]")).toHaveLength(0);
  });

  it("an unknown event kind or stage name from a newer server shows as text", async () => {
    const events = {
      body: {
        data: [{ ...activityEvents.body.data[0], kind: PAYLOAD, stage: PAYLOAD, outcome: PAYLOAD, task: "task-0136" }],
        meta: { ...activityEvents.body.meta, total: 1 },
      },
    };
    await renderApp("/activity", { routes: { "/api/v1/events": fromFixture(events) } });
    await settle(250);

    expect(document.body.querySelectorAll("script, img, iframe, svg[onload]")).toHaveLength(0);
    expect(screen.getByRole("main")).toHaveTextContent("alert(");
  });
});

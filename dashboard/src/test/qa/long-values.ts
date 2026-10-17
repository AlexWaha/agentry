import { screen } from "@testing-library/react";

import activityEvents from "@/test/fixtures/activity/events.json";
import metricsFixture from "@/test/fixtures/activity/metrics.json";
import handoffFixture from "@/test/fixtures/harness/handoff.json";
import handoffsFixture from "@/test/fixtures/harness/handoffs.json";
import harnessFixture from "@/test/fixtures/harness/harness.json";
import codegraphFixture from "@/test/fixtures/memory/codegraph.json";
import lessonsFixture from "@/test/fixtures/memory/lessons.json";
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

/*
 * Helpers for the long-value checks (FR-128): every free-text field of the API answers turned into a 120-character
 * value without a space, and a DOM walk that finds each place such a value is printed and judges whether the
 * element can hold it (a wrapping class, or an ellipsis class WITH the full text in a title).
 */

export const LONG_LENGTH = 120;

/** Fields the UI switches on, and ids that routes and links are built from. */
const KEEP_KEYS = new Set([
  "folder", "state", "delivery", "install", "pipeline", "mode", "approvals", "kind", "profile", "level", "protocol", "status",
  "id", "task", "key", "outcome", "stage", "from_stage", "lane", "source", "event", "command", "script", "type", "priority", "spec", "epic", "assignee",
]);

/** A value of exactly LONG_LENGTH characters with no space, recognisable by its field name. */
export function longValue(key: string): string {
  return `${key.toUpperCase()}_`.padEnd(LONG_LENGTH, "W");
}

export function longify(value: unknown, key = ""): unknown {
  if (typeof value === "string") return KEEP_KEYS.has(key) || /^\d{4}-\d{2}-\d{2}/.test(value) || value === "" ? value : longValue(key);
  if (Array.isArray(value)) return value.map((item) => longify(item, key));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, longify(item, name)]));
  return value;
}

const serve = (fixture: { body: { data: unknown; meta?: unknown } }): MockHandler => {
  const body = longify(fixture.body) as { data: unknown; meta?: unknown };
  return ok(body.data, body.meta);
};

/** Fields kept as-is above but which hold free text in a few shapes; they get the long value too. */
function longTyped(fixture: { body: { data: unknown; meta?: unknown } }, keys: string[]): MockHandler {
  const body = longify(fixture.body) as { data: unknown; meta?: unknown };
  const widen = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(widen);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, keys.includes(name) && typeof item === "string" && item !== "" ? longValue(name) : widen(item)]));
    }
    return value;
  };
  const widened = widen(body) as { data: unknown; meta?: unknown };
  return ok(widened.data, widened.meta);
}

export const LONG_ROUTES: Record<string, MockHandler> = {
  "/api/v1/status": ok(statusFixture.body.data),
  "/api/v1/runs": serve(runsFixture),
  "/api/v1/tasks": longTyped(tasksFixture, ["type", "epic", "assignee", "priority"]),
  "/api/v1/tasks/task-0104": longTyped(taskDetailFixture, ["type", "epic", "assignee", "spec"]),
  "/api/v1/epics": serve(epicsFixture),
  "/api/v1/events": (url) => (url.searchParams.get("limit") === "5" ? serve(eventsFixture) : serve(activityEvents)) as never,
  "/api/v1/metrics": serve(metricsFixture),
  "/api/v1/memory": (url) => {
    const kind = url.searchParams.get("kind");
    return (kind === "pattern" ? serve(patternsFixture) : kind === "module" ? serve(modulesFixture) : longTyped(lessonsFixture, ["task", "agent"])) as never;
  },
  "/api/v1/memory/stats": serve(memoryStatsFixture),
  "/api/v1/codegraph": serve(codegraphFixture),
  "/api/v1/harness": longTyped(harnessFixture, ["name", "source", "command", "script", "matcher", "department", "model"]),
  "/api/v1/handoffs": longTyped(handoffsFixture, ["author"]),
  "/api/v1/handoffs/task-0107": serve(handoffFixture),
};

const WRAPS = /(?:^|\s)(?:wrap-anywhere|break-all|break-words|\[overflow-wrap:anywhere\])(?:\s|$)/;
const ELLIPSIS = /(?:^|\s)(?:truncate|text-ellipsis)(?:\s|$)/;
const CLIPS = /(?:^|\s)(?:overflow-(?:hidden|auto|x-auto|scroll)|overflow-x-(?:auto|hidden|scroll))(?:\s|$)/;

export interface Offender {
  text: string;
  tag: string;
  className: string;
  reason: string;
}

const classOf = (element: Element): string => element.getAttribute("class") ?? "";

/**
 * Every element that prints a LONG_LENGTH-character unbroken value, judged by classes (jsdom has no layout):
 * - the element or an ancestor inside `main` wraps (`wrap-anywhere`), or
 * - it or an ancestor ellipsizes AND carries the full text in a `title`, or
 * - an ancestor scrolls on its own (a table or code block).
 * Anything else is a value that can push its container wider than the viewport.
 */
export function findOverflowRisks(root: Element = screen.getByRole("main")): Offender[] {
  const out: Offender[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent ?? "";
    const token = text.split(/\s+/).find((word) => word.length >= LONG_LENGTH - 5);
    const parent = node.parentElement;
    if (!token || !parent || parent.closest("svg, option, title")) continue;

    let wraps = false;
    let clipped = false;
    let ellipsized = false;
    let titled = false;
    for (let element: Element | null = parent; element && element !== root.parentElement; element = element.parentElement) {
      const classes = classOf(element);
      if (WRAPS.test(classes)) wraps = true;
      if (CLIPS.test(classes)) clipped = true;
      if (ELLIPSIS.test(classes)) {
        ellipsized = true;
        if ((element.getAttribute("title") ?? "").includes(token.slice(0, 20))) titled = true;
      }
    }
    if (wraps || clipped) continue;
    if (ellipsized && titled) continue;
    out.push({
      text: token.slice(0, 24),
      tag: parent.tagName.toLowerCase(),
      className: classOf(parent).slice(0, 110),
      reason: ellipsized ? "ellipsis without the full text in a title" : "no wrapping class on the element or an ancestor",
    });
  }
  return out;
}

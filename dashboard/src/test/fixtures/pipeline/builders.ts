import { ok, type MockResponse } from "@/test/utils/api-mock";
import type { Folder, TaskSummary, TasksMeta } from "@/types/api";

/** A TaskSummary with sensible defaults; the tests of the pipeline pages build their lists with it. */
export function summary(id: string, folder: Folder, overrides: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id,
    title: `Title of ${id}`,
    type: "feature",
    priority: "medium",
    epic: "",
    spec: "none",
    assignee: "senior-backend-dev",
    folder,
    created: "2026-10-01T00:00:00Z",
    completed: "",
    blocked_on: "",
    superseded_by: "",
    depends_on: [],
    ...overrides,
  };
}

/** `count` tasks of one folder with ids task-<start>, task-<start + 1>, ... */
export function manyTasks(folder: Folder, count: number, start: number, overrides: Partial<TaskSummary> = {}): TaskSummary[] {
  return Array.from({ length: count }, (_, index) => summary(`task-${String(start + index).padStart(4, "0")}`, folder, overrides));
}

/** A mock answer made of a fixture file's body (`{data, meta?}`). */
export function fromFixture(fixture: { body: { data: unknown; meta?: unknown } }): MockResponse {
  return ok(fixture.body.data, fixture.body.meta);
}

/** A `/tasks` answer: the rows of this page, the exact total and the meta the server adds (FR-21). */
export function tasksAnswer(rows: TaskSummary[], total: number, meta: Partial<TasksMeta> = {}): MockResponse {
  return ok(rows, {
    total,
    limit: 25,
    offset: 0,
    hidden_superseded: 0,
    facets: { folders: { backlog: 0, active: 0, done: total }, types: ["bugfix", "feature"] },
    ...meta,
  });
}

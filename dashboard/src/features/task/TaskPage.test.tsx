import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ROUTES } from "@/config/routes";
import { startWsClient } from "@/lib/ws-client";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import doneFixture from "@/test/fixtures/pipeline/task-detail-done.json";
import detailFixture from "@/test/fixtures/pipeline/task-detail.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { FakeWebSocket, helloMessage, installFakeWebSocket, invalidateMessage } from "@/test/utils/fake-ws";
import { renderWithApp } from "@/test/utils/render";
import type { Stage, TaskDetail } from "@/types/api";

import { TaskPage } from "./TaskPage";

const detail = detailFixture.body.data as unknown as TaskDetail;
const done = doneFixture.body.data as unknown as TaskDetail;

const route = (id: string) => `/api/v1/tasks/${id}`;

function render(id: string, shell = false) {
  return renderWithApp(<TaskPage />, { url: `/tasks/${id}`, path: ROUTES.task, shell });
}

function withDetail(overrides: Partial<TaskDetail>) {
  return ok({ ...detail, ...overrides });
}

const section = (title: string) => within(screen.getByRole("heading", { name: title }).closest("section") as HTMLElement);

describe("TaskPage", () => {
  it("shows a loading state while the task is on its way", async () => {
    mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    await render("task-0104");

    expect(screen.getByRole("status")).toHaveTextContent("Loading the task");
    expect(await screen.findByRole("heading", { level: 1, name: "Delta in flight" })).toBeInTheDocument();
  });

  it("renders the header fields and the depends_on links", async () => {
    mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    await render("task-0104");

    const title = await screen.findByRole("heading", { level: 1, name: "Delta in flight" });
    const header = within(title.closest("header") as HTMLElement);
    expect(header.getByText("task-0104")).toBeInTheDocument();
    expect(header.getByText("Active")).toBeInTheDocument();
    expect(header.getByText("Needs you: commit")).toBeInTheDocument();
    expect(header.getByText("feature")).toBeInTheDocument();
    expect(header.getByText("High")).toBeInTheDocument();
    expect(header.getByText("epic-0001")).toBeInTheDocument();
    expect(header.getByText("none")).toBeInTheDocument();
    expect(header.getByText("senior-backend-dev")).toBeInTheDocument();
    expect(header.getByRole("link", { name: "task-0106" })).toHaveAttribute("href", "/tasks/task-0106");
    expect(screen.getByRole("link", { name: "Back to Tasks" })).toHaveAttribute("href", "/tasks");
  });

  it("shows 3 of 5 done for the acceptance criteria, the checkbox states and the sections that exist", async () => {
    mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    await render("task-0104");

    await screen.findByRole("heading", { name: "Acceptance Criteria" });
    const criteria = section("Acceptance Criteria");
    expect(criteria.getByText("3 of 5 done")).toBeInTheDocument();
    const boxes = criteria.getAllByRole("checkbox");
    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, true, false, false]);
    expect(section("Definition of Done").getByText("1 of 2 done")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Description" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Frontend Impact" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Deploy Actions" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Backend Dependencies" })).not.toBeInTheDocument();
    expect(screen.queryByText(/No status: or branch: field/)).not.toBeInTheDocument();
  });

  it("orders the sections Description, Acceptance Criteria, Definition of Done, Impact, Deploy Actions, Notes", async () => {
    mockApi({
      [route("task-0104")]: withDetail({ body: "## Notes\n\nn\n\n## Deploy Actions\n\nd\n\n## Definition of Done\n\n- [ ] x\n\n## Description\n\nt\n\n## Acceptance Criteria\n\n- [ ] y\n" }),
    });
    await render("task-0104");

    await screen.findByRole("heading", { name: "Notes" });
    const titles = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent)
      .filter((text) => text !== "Pipeline" && text !== "Dependencies");
    expect(titles).toEqual(["Description", "Acceptance Criteria", "Definition of Done", "Deploy Actions", "Notes"]);
  });

  it("renders text from the body as inert text and task ids as links", async () => {
    mockApi({
      [route("task-0104")]: withDetail({
        body: "## Description\n\n<script>alert(1)</script> and [bad](javascript:alert(1)) and task-0106\n",
      }),
    });
    await render("task-0104");

    await screen.findByRole("heading", { name: "Description" });
    const description = section("Description");
    expect(await description.findByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(description.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
    expect(description.getByRole("link", { name: "task-0106" })).toHaveAttribute("href", "/tasks/task-0106");
  });

  it("lists depends_on and dependents as links in the Dependencies block", async () => {
    mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    await render("task-0104");

    await screen.findByRole("heading", { name: "Dependencies" });
    const block = section("Dependencies");
    expect(block.getByRole("link", { name: "task-0106" })).toHaveAttribute("href", "/tasks/task-0106");
    expect(block.getByText("Zeta done")).toBeInTheDocument();
    expect(block.getByRole("link", { name: "task-0105" })).toHaveAttribute("href", "/tasks/task-0105");
    expect(block.getByText("Epsilon awaiting commit")).toBeInTheDocument();
  });

  it("shows the notice when the body was cut", async () => {
    mockApi({ [route("task-0104")]: withDetail({ body_truncated: true }) });
    await render("task-0104");

    expect(await screen.findByText(/longer than the dashboard shows/)).toBeInTheDocument();
  });

  it("shows the task not-found page for an id the API does not know", async () => {
    mockApi({ [route("task-9999")]: fail(404, "not_found", "No such task") });
    await render("task-9999");

    expect(await screen.findByRole("heading", { name: "Task not found" })).toBeInTheDocument();
    expect(screen.getByText("task-9999 is not in this project.")).toBeInTheDocument();
  });

  it("shows the error with a retry that loads the task", async () => {
    const api = mockApi({ [route("task-0104")]: fail(500, "internal", "Store failed") });
    await render("task-0104");

    expect(await screen.findByRole("alert")).toHaveTextContent("Store failed");
    api.set(route("task-0104"), fromFixture(detailFixture));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Delta in flight" })).toBeInTheDocument();
  });

  it("says why the pipeline block is empty when the task has no events", async () => {
    mockApi({ [route("task-0104")]: withDetail({ body: "", events: [], gate_runs: 0, gate_failures: 0, rejections: 0 }) });
    await render("task-0104");

    expect(await screen.findByText("No events recorded for this task yet.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Description" })).not.toBeInTheDocument();
  });

  describe("Pipeline block (FR-98)", () => {
    it("shows the current step, the human checkpoints and the gate counts", async () => {
      mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
      await render("task-0104");

      await screen.findByRole("heading", { name: "Pipeline" });
      expect(screen.getByText("Gate runs: 2, failed: 1, rejections: 1")).toBeInTheDocument();
      const current = document.querySelector('li[aria-current="step"]') as HTMLElement;
      expect(current).toHaveTextContent("Commit approval");
      expect(current).toHaveTextContent("Waiting for you");
      const humans = detail.stages.filter((stage) => stage.human).map((stage) => stage.name);
      expect(humans).toEqual(["plan-approval", "ready"]);
      expect(document.querySelectorAll("ol > li[data-state]")).toHaveLength(detail.stages.length);
      expect(screen.getByText("1 ok, 1 failed")).toBeInTheDocument();
    });

    it("shows a skipped step as skipped", async () => {
      const stages: Stage[] = detail.stages.map((stage) => (stage.name === "plan" ? { ...stage, state: "skipped", pass: 0 } : stage));
      mockApi({ [route("task-0104")]: withDetail({ stages }) });
      await render("task-0104");

      await screen.findByRole("heading", { name: "Pipeline" });
      const skipped = document.querySelector('li[data-state="skipped"]') as HTMLElement;
      expect(skipped).toHaveTextContent("Plan");
      expect(skipped).toHaveTextContent("skipped");
    });

    it("shows the final step of an abandoned run as Abandoned, not Merged", async () => {
      const stages: Stage[] = detail.stages.map((stage) => ({ ...stage, state: "done" }));
      const run = { ...(detail.run as NonNullable<TaskDetail["run"]>), stage: "done", stage_status: "abandoned", needs_you: null };
      mockApi({ [route("task-0104")]: withDetail({ stages, run }) });
      await render("task-0104");

      await screen.findByRole("heading", { name: "Pipeline" });
      expect(within(document.querySelector("ol > li:last-child") as HTMLElement).getByText("Abandoned")).toBeInTheDocument();
      expect(document.querySelector('li[aria-current="step"]')).toBeNull();
    });

    it("shows a gate with its result and duration and a rejection with its reason", async () => {
      mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
      await render("task-0104");

      const timeline = within(await screen.findByRole("list", { name: "Task history" }));
      const passed = timeline.getByText("Gate passed: Implement").closest("li") as HTMLElement;
      expect(passed).toHaveTextContent("1m 01s");
      expect(timeline.getByText("Gate failed: Implement").closest("li")).toHaveTextContent("4s");
      const rejected = timeline.getByText("Rejected, sent back to Implement").closest("li") as HTMLElement;
      expect(rejected).toHaveTextContent("needs a test");
    });

    it("shows the timeline only, with the merge, for a task without a run row", async () => {
      mockApi({ [route("task-0107")]: fromFixture(doneFixture) });
      await render("task-0107");

      const timeline = within(await screen.findByRole("list", { name: "Task history" }));
      expect(timeline.getByText("Merged")).toBeInTheDocument();
      expect(timeline.getByText("Gate passed: Test").closest("li")).toHaveTextContent("2m 05s");
      expect(done.run).toBeNull();
      expect(document.querySelector('li[aria-current="step"]')).toBeNull();
      expect(document.querySelectorAll("ol > li[data-state]")).toHaveLength(0);
    });

    it("tells that only the newest events are shown when the journal was cut", async () => {
      mockApi({ [route("task-0104")]: withDetail({ events_truncated: true }) });
      await render("task-0104");

      expect(await screen.findByText(/newest events only/)).toBeInTheDocument();
    });
  });

  it("has no axe violation in the data state", async () => {
    mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    await render("task-0104", true);

    await screen.findByRole("heading", { name: "Pipeline" });
    await expectNoAxeViolations(document.body);
  });

  it("refetches on an invalidate message without a reload", async () => {
    installFakeWebSocket();
    const api = mockApi({ [route("task-0104")]: fromFixture(detailFixture) });
    const scrollTo = vi.spyOn(window, "scrollTo");
    const { queryClient } = await render("task-0104");
    const stop = startWsClient(queryClient);
    try {
      await screen.findByRole("heading", { level: 1, name: "Delta in flight" });
      FakeWebSocket.last.open();
      FakeWebSocket.last.receive(helloMessage);
      await waitFor(() => expect(api.callsTo(route("task-0104")).length).toBeGreaterThanOrEqual(2));
      api.set(route("task-0104"), withDetail({ title: "Delta renamed" }));
      scrollTo.mockClear();

      FakeWebSocket.last.receive(invalidateMessage("tasks"));

      expect(await screen.findByRole("heading", { level: 1, name: "Delta renamed" }, { timeout: 3000 })).toBeInTheDocument();
      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      stop();
    }
  });
});

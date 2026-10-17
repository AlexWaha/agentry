import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fromFixture } from "@/test/fixtures/pipeline/builders";
import runsFixture from "@/test/fixtures/shell/runs.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";
import type { Run } from "@/types/api";

import { NeedsPage } from "./NeedsPage";

const base = runsFixture.body.data[0] as unknown as Run;

function run(task: string, title: string, since: string, kind: string, reason: string): Run {
  return { ...base, task, title, needs_you: { kind, since, reason } };
}

/** Listed newest first on purpose: the page must reorder by `since`. */
const WAITING: Run[] = [
  run("task-0002", "Newer wait", "2026-10-16T10:00:00Z", "commit", "Commit approval"),
  run("task-0003", "Older wait", "2026-10-16T08:00:00Z", "plan", "Plan approval"),
  { ...base, task: "task-0004", title: "Running fine", needs_you: null },
];

describe("NeedsPage", () => {
  it("shows a loading state while the runs are on their way", async () => {
    mockApi({ "/api/v1/runs": fromFixture(runsFixture) });
    await renderWithApp(<NeedsPage />, { url: "/needs" });

    expect(screen.getByRole("status")).toHaveTextContent("Loading the runs that wait for you");
    expect(await screen.findByRole("list", { name: "Runs waiting for you" })).toBeInTheDocument();
  });

  it("lists the waiting runs oldest first with the kind chip, reason and waiting time", async () => {
    mockApi({ "/api/v1/runs": ok(WAITING) });
    await renderWithApp(<NeedsPage />, { url: "/needs" });

    const rows = within(await screen.findByRole("list", { name: "Runs waiting for you" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("task-0003");
    expect(rows[0]).toHaveTextContent("Older wait");
    expect(rows[0].querySelector('[data-status="need"]')).toHaveTextContent("Plan approval");
    expect(rows[0]).toHaveTextContent("Waiting since 10-16 08:00:00Z");
    expect(rows[0]).toHaveTextContent("ago");
    expect(rows[1]).toHaveTextContent("task-0002");
    expect(rows[1].querySelector('[data-status="need"]')).toHaveTextContent("Commit approval");
    expect(within(rows[0]).getByRole("link", { name: "task-0003" })).toHaveAttribute("href", "/tasks/task-0003");
    expect(screen.queryByText("Running fine")).not.toBeInTheDocument();
  });

  it("names a blocked run and shows an unknown kind as written", async () => {
    mockApi({
      "/api/v1/runs": ok([
        run("task-0005", "Stuck", "2026-10-16T08:00:00Z", "blocked", "Gate failed 3 times"),
        run("task-0006", "Odd", "2026-10-16T09:00:00Z", "deploy", "Custom checkpoint"),
      ]),
    });
    await renderWithApp(<NeedsPage />, { url: "/needs" });

    const chips = (await screen.findAllByText(/Blocked|deploy/)).filter((node) => node.closest("[data-status]"));
    expect(chips.map((node) => node.textContent)).toEqual(["Blocked", "deploy"]);
    expect(screen.getByText("Gate failed 3 times")).toBeInTheDocument();
  });

  it("says nothing is waiting when no run needs the User", async () => {
    mockApi({ "/api/v1/runs": ok([{ ...base, needs_you: null }]) });
    await renderWithApp(<NeedsPage />, { url: "/needs" });

    expect(await screen.findByText("Nothing is waiting for you.")).toBeInTheDocument();
    expect(screen.getByText(/No run is parked at an approval/)).toBeInTheDocument();
  });

  it("shows the error with a retry that loads the list", async () => {
    const api = mockApi({ "/api/v1/runs": fail(500, "internal", "Store failed") });
    await renderWithApp(<NeedsPage />, { url: "/needs" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Store failed");
    api.set("/api/v1/runs", ok(WAITING));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("list", { name: "Runs waiting for you" })).toBeInTheDocument();
  });

  it("matches the sidebar count to the length of the list and has no axe violation", async () => {
    mockApi({ "/api/v1/runs": ok(WAITING) });
    await renderWithApp(<NeedsPage />, { url: "/needs", shell: true });

    const list = await screen.findByRole("list", { name: "Runs waiting for you" });
    const count = await screen.findByLabelText("2 waiting for you");

    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(count).toHaveTextContent("2");
    await expectNoAxeViolations(document.body);
  });
});

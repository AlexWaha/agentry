import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import eventsFixture from "@/test/fixtures/pipeline/events.json";
import { fromFixture } from "@/test/fixtures/pipeline/builders";
import runsFixture from "@/test/fixtures/shell/runs.json";
import statusFixture from "@/test/fixtures/shell/status.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";
import type { Run, Status } from "@/types/api";

import { OverviewPage } from "./OverviewPage";

const status = statusFixture.body.data as unknown as Status;
const runs = runsFixture.body.data as unknown as Run[];

function serve(overrides: Record<string, ReturnType<typeof ok>> = {}) {
  return mockApi({
    "/api/v1/status": fromFixture(statusFixture),
    "/api/v1/runs": fromFixture(runsFixture),
    "/api/v1/events": fromFixture(eventsFixture),
    ...overrides,
  });
}

describe("OverviewPage", () => {
  it("shows a loading state while the data is on its way", async () => {
    serve();
    await renderWithApp(<OverviewPage />, { url: "/" });

    expect(screen.getByRole("status")).toHaveTextContent("Loading the overview");
    expect(await screen.findByRole("heading", { name: "Current runs" })).toBeInTheDocument();
  });

  it("shows the four counts, one stepper per run, five events and the debt links", async () => {
    const api = serve();
    await renderWithApp(<OverviewPage />, { url: "/", shell: true });

    await screen.findByRole("heading", { name: "Current runs" });
    const counts = within(screen.getByLabelText("Task counts"));
    expect(counts.getByText("Backlog").parentElement).toHaveTextContent(`Backlog${status.counts.backlog}`);
    expect(counts.getByText("Active").parentElement).toHaveTextContent(`Active${status.counts.active}`);
    expect(counts.getByText("Done").parentElement).toHaveTextContent(`Done${status.counts.done}`);
    expect(counts.getByText("Needs you").parentElement).toHaveTextContent("Needs you1");

    expect(document.querySelectorAll('li[aria-current="step"]')).toHaveLength(runs.length);
    expect(within(screen.getByRole("list", { name: "Latest events" })).getAllByRole("listitem")).toHaveLength(5);
    expect(api.callsTo("/api/v1/events")[0].url.search).toBe("?limit=5");

    expect(screen.getByRole("link", { name: "Handoffs owed: 1" })).toHaveAttribute("href", "/handoffs");
    expect(screen.getByRole("link", { name: "Memory reviews owed: 1" })).toHaveAttribute("href", "/handoffs");
    expect(screen.getByRole("link", { name: "Open the list" })).toHaveAttribute("href", "/needs");
    expect(screen.getByRole("link", { name: "task-0001" })).toHaveAttribute("href", "/tasks/task-0001");
    expect(screen.getAllByRole("link", { name: "task-0104" })[0]).toHaveAttribute("href", "/tasks/task-0104");
    await expectNoAxeViolations(document.body);
  });

  it("says why it is empty when no run is in flight and the journal is empty", async () => {
    serve({
      "/api/v1/runs": ok([]),
      "/api/v1/events": ok([], { total: 0, limit: 5, offset: 0, latest_id: 0, kinds: [] }),
    });
    await renderWithApp(<OverviewPage />, { url: "/" });

    expect(await screen.findByText("Idle.")).toBeInTheDocument();
    expect(screen.getByText("No run is in flight.")).toBeInTheDocument();
    expect(screen.getByText("No events yet.")).toBeInTheDocument();
  });

  it("shows the error with a retry that loads the data", async () => {
    const api = serve({ "/api/v1/status": fail(500, "internal", "Store failed") });
    await renderWithApp(<OverviewPage />, { url: "/" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Store failed");
    api.set("/api/v1/status", fromFixture(statusFixture));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "Current runs" })).toBeInTheDocument();
  });
});

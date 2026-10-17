import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ROUTES } from "@/config/routes";
import metricsFixture from "@/test/fixtures/activity/metrics.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";

import { MetricsPage } from "./MetricsPage";
import { validateMetricsSearch } from "./search";

const METRICS_PATH = "/api/v1/metrics";
const DATA = metricsFixture.body.data;

async function renderPage(url = "/metrics") {
  return renderWithApp(<MetricsPage />, { url, path: ROUTES.metrics, validateSearch: validateMetricsSearch });
}

function emptyMetrics() {
  return { ...structuredClone(DATA), span: "", events_total: 0, kinds: {}, gates: [], slowest: [], dwell: [], per_day: [], rejections: { total: 0, latest: [] }, merges_total: 0, lead_time_s: null };
}

/** The bars of a chart: one `rect` per category and series inside the chart's svg. */
function bars(chartName: string): number {
  return screen.getByRole("img", { name: chartName }).querySelectorAll("rect").length;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Metrics states", () => {
  it("shows a loading status while the first answer is on its way", async () => {
    vi.stubGlobal("fetch", () => new Promise(() => undefined));

    await renderPage();

    expect(screen.getByRole("status", { name: "Loading metrics" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Time range" })).toBeInTheDocument();
  });

  it("shows the empty state when the range holds no events", async () => {
    mockApi({ [METRICS_PATH]: ok(emptyMetrics()) });

    await renderPage();

    expect(await screen.findByText("No events in this range.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("shows the error state and loads the page when Retry succeeds", async () => {
    const api = mockApi({ [METRICS_PATH]: fail(500, "server_error", "The journal could not be read.") });
    await renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("The journal could not be read.");

    api.set(METRICS_PATH, ok(DATA));
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("img", { name: "Gate duration per stage" })).toBeInTheDocument();
  });

  it("shows the numbers of the fixture and passes axe in the data state", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });

    const { container } = await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });

    expect(screen.getByText("96 events from 2026-10-10 to 2026-10-16.")).toBeInTheDocument();
    const merges = screen.getByText("Merges").parentElement as HTMLElement;
    expect(merges).toHaveTextContent("3");
    const leadTime = screen.getByText("Lead time").parentElement as HTMLElement;
    expect(leadTime).toHaveTextContent("1d 2h");
    const rejections = screen.getByText("Rejections").parentElement as HTMLElement;
    expect(rejections).toHaveTextContent("2");
    await expectNoAxeViolations(container);
  });

  it("shows a dash for the lead time when nothing merged in the range", async () => {
    mockApi({ [METRICS_PATH]: ok({ ...structuredClone(DATA), lead_time_s: null, merges_total: 0 }) });

    await renderPage();

    const leadTime = (await screen.findByText("Lead time")).parentElement as HTMLElement;
    expect(leadTime).toHaveTextContent("-");
  });
});

describe("Metrics range control (FR-101)", () => {
  it("asks for 14 days first and offers 7, 14, 30 and 90", async () => {
    const api = mockApi({ [METRICS_PATH]: ok(DATA) });

    await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });
    const select = screen.getByRole("combobox", { name: "Time range" });

    expect(api.requests[0].url.searchParams.get("days")).toBe("14");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Last 7 days",
      "Last 14 days",
      "Last 30 days",
      "Last 90 days",
    ]);
    expect(select).toHaveValue("14");
  });

  it.each([
    ["Last 7 days", "7", { days: 7 }],
    ["Last 30 days", "30", { days: 30 }],
    ["Last 90 days", "90", { days: 90 }],
  ])("sends days for %s and keeps it in the URL", async (option, days, search) => {
    const api = mockApi({ [METRICS_PATH]: ok(DATA) });
    const { router } = await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });

    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Time range" }), option);

    await waitFor(() => expect(api.requests.at(-1)?.url.searchParams.get("days")).toBe(days));
    expect(router.state.location.search).toEqual(search);
  });

  it("omits the default range from the URL and restores a range from the URL", async () => {
    const api = mockApi({ [METRICS_PATH]: ok(DATA) });
    const { router } = await renderPage("/metrics?days=30");

    expect(await screen.findByRole("combobox", { name: "Time range" })).toHaveValue("30");
    expect(api.requests[0].url.searchParams.get("days")).toBe("30");

    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Time range" }), "Last 14 days");

    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("drops a range the API does not offer", () => {
    expect(validateMetricsSearch({ days: 45 })).toEqual({ days: undefined });
    expect(validateMetricsSearch({ days: "abc" })).toEqual({ days: undefined });
    expect(validateMetricsSearch({ days: 14 })).toEqual({ days: undefined });
    expect(validateMetricsSearch({ days: 7 })).toEqual({ days: 7 });
  });
});

describe("Metrics charts (FR-101, FR-102)", () => {
  it("draws one bar per stage and series, per slow gate and per day and series", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });

    await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });

    expect(bars("Gate duration per stage")).toBe(9);
    expect(bars("Gate failures per stage")).toBe(6);
    expect(bars("Slowest gates")).toBe(2);
    expect(bars("Stage dwell")).toBe(6);
    expect(bars("Activity per day")).toBe(12);
  });

  it("gives every chart a name and a description", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });

    await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });

    expect(screen.getAllByRole("img")).toHaveLength(5);
    expect(screen.getByRole("img", { name: "Gate duration per stage" })).toHaveAccessibleDescription(
      "Median, 90th percentile and slowest gate run for each stage.",
    );
    expect(screen.getByRole("img", { name: "Activity per day" })).toHaveAccessibleDescription(
      "Gate runs, failed gate runs, merges and rejections for each day with events.",
    );
  });

  it("keeps the table hidden until Show as table is chosen, then shows the same numbers", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });
    await renderPage();
    await screen.findByRole("img", { name: "Gate duration per stage" });
    const user = userEvent.setup();

    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Show as table" })[0]);

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("row", { name: "Stage Median 90th percentile Slowest" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "Implement 4s 10s 12s" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "Test 38s 1m 01s 1m 15s" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "Review 3s 5s 8s" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide table" })).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Hide table" }));

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("lists the failures per stage and the per-day counts in their tables", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });
    await renderPage();
    await screen.findByRole("img", { name: "Gate failures per stage" });
    const user = userEvent.setup();

    for (const button of screen.getAllByRole("button", { name: "Show as table" })) await user.click(button);

    const failures = screen.getByRole("region", { name: "Gate failures per stage, as a table" });
    expect(within(failures).getByRole("row", { name: "Test 14 2" })).toBeInTheDocument();
    const perDay = screen.getByRole("region", { name: "Activity per day, as a table" });
    expect(within(perDay).getByRole("row", { name: "2026-10-15 18 3 1 1" })).toBeInTheDocument();
    const slowest = screen.getByRole("region", { name: "Slowest gates, as a table" });
    expect(within(slowest).getByRole("row", { name: "task-0136 Test 1m 01s Failed 10-16 08:40:30Z" })).toBeInTheDocument();
    const dwell = screen.getByRole("region", { name: "Stage dwell, as a table" });
    expect(within(dwell).getByRole("row", { name: "Implement 9 1h 30m 4h 00m 6h 00m" })).toBeInTheDocument();
  });

  it("shows the empty state in a chart that has no data", async () => {
    mockApi({ [METRICS_PATH]: ok({ ...structuredClone(DATA), gates: [], slowest: [] }) });

    await renderPage();
    await screen.findByRole("img", { name: "Stage dwell" });

    expect(screen.getAllByText("Nothing to chart yet.")).toHaveLength(3);
    expect(screen.queryByRole("img", { name: "Gate duration per stage" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Show as table" })).toHaveLength(2);
  });

  it("lists the latest rejections with their reason", async () => {
    mockApi({ [METRICS_PATH]: ok(DATA) });

    await renderPage();

    const heading = await screen.findByRole("heading", { name: "Latest rejections" });
    const card = heading.parentElement?.parentElement as HTMLElement;
    expect(within(card).getByText("2 in the range")).toBeInTheDocument();
    expect(within(card).getByText("The diff drops the empty state of the Board.")).toBeInTheDocument();
    expect(within(card).getByText("No reason recorded.")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "task-0136" })).toHaveAttribute("href", "/tasks/task-0136");
  });

  it("says so when the range has no rejection", async () => {
    mockApi({ [METRICS_PATH]: ok({ ...structuredClone(DATA), rejections: { total: 0, latest: [] } }) });

    await renderPage();

    expect(await screen.findByText("No rejections in this range.")).toBeInTheDocument();
  });
});

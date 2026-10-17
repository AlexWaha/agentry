import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DOCS_BLOB_URL } from "@/config/constants";
import docsLinks from "@/config/docs-links.json";
import { ROUTES } from "@/config/routes";
import handoffsFixture from "@/test/fixtures/harness/handoffs.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";
import type { Handoff } from "@/types/api";

import { HandoffsPage } from "./HandoffsPage";
import { validateHandoffsSearch } from "./search";

const handoffs = handoffsFixture.body.data as Handoff[];
const TOTAL = 60;
const SIZE = 25;

/** Sixty documents, newest first: task-0160 down to task-0101. */
const sixty: Handoff[] = Array.from({ length: TOTAL }, (_, index) => ({
  task: `task-${160 - index}`,
  title: `Handoff ${160 - index}`,
  author: "senior-backend-dev",
  date: "2026-10-15",
  bytes: 2048,
  valid: index % 2 === 0,
}));

const pagedHandler = (url: URL) => {
  const offset = Number(url.searchParams.get("offset") ?? "0");
  const limit = Number(url.searchParams.get("limit") ?? SIZE);
  return ok(sixty.slice(offset, offset + limit), { total: TOTAL, limit, offset });
};

async function open(url = "/handoffs") {
  return renderWithApp(<HandoffsPage />, { url, path: ROUTES.handoffs, validateSearch: validateHandoffsSearch });
}

describe("Handoffs list", () => {
  it("lists the documents newest first with task, title, author, date, size and a valid or invalid chip", async () => {
    mockApi({ "/api/v1/handoffs": ok(handoffs, { total: 3, limit: SIZE, offset: 0 }) });
    await open();

    const list = await screen.findByRole("list", { name: "Handoff documents" });
    const rows = within(list).getAllByRole("listitem");

    expect(rows.map((row) => within(row).getByRole("link").textContent)).toEqual(["task-0107", "task-0106", "task-0105"]);
    expect(within(rows[0]).getByText("Expose the quality gate as a verify skill")).toBeInTheDocument();
    expect(within(rows[0]).getByText("by senior-backend-dev")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2026-10-15")).toBeInTheDocument();
    expect(within(rows[0]).getByText("1009 B")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Invalid")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Valid")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", "/handoffs/task-0107");
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("1-3 of 3 handoffs");
  });

  it("requests 25 per page and offset=25 for page 2, and shows the total in the pager", async () => {
    const api = mockApi({ "/api/v1/handoffs": pagedHandler });
    await open("/handoffs?page=2");

    expect(await screen.findByText("task-135")).toBeInTheDocument();

    const request = api.callsTo("/api/v1/handoffs")[0];
    expect(request.url.searchParams.get("limit")).toBe("25");
    expect(request.url.searchParams.get("offset")).toBe("25");
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("26-50 of 60 handoffs");
    expect(screen.getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
  });

  it("moves to the next page through the pager and writes it into the URL", async () => {
    const api = mockApi({ "/api/v1/handoffs": pagedHandler });
    const { router } = await open();
    await screen.findByText("task-160");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("task-135")).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ page: 2 });
    expect(api.callsTo("/api/v1/handoffs").at(-1)?.url.searchParams.get("offset")).toBe("25");
  });

  it("replaces a page beyond the last one by the last page in the URL", async () => {
    mockApi({ "/api/v1/handoffs": pagedHandler });
    const { router } = await open("/handoffs?page=99");

    await waitFor(() => expect(router.state.location.search).toEqual({ page: 3 }));
    expect(await screen.findByText("task-110")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("51-60 of 60 handoffs");
  });

  it("shows page 1 for ?page=abc and ?page=0", async () => {
    mockApi({ "/api/v1/handoffs": pagedHandler });
    const { router } = await open("/handoffs?page=abc");

    expect(await screen.findByText("task-160")).toBeInTheDocument();
    expect(router.state.location.search).toEqual({});
  });
});

describe("Handoffs page states", () => {
  it("shows a skeleton while loading, with the header and docs link already there", async () => {
    const api = mockApi();
    api.fetch.mockImplementation(() => new Promise<Response>(() => undefined));
    await open();

    expect(screen.getByRole("status", { name: "Loading the handoffs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Handoffs" })).toBeInTheDocument();
  });

  it("says why the list is empty and shows no pager", async () => {
    mockApi({ "/api/v1/handoffs": ok([], { total: 0, limit: SIZE, offset: 0 }) });
    await open();

    expect(await screen.findByText("No handoff documents yet.")).toBeInTheDocument();
    expect(screen.getByText(/once a finished task has one/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /handoffs/i })).not.toBeInTheDocument();
  });

  it("shows the server message and retries on click", async () => {
    const api = mockApi({ "/api/v1/handoffs": fail(500, "internal", "Handoffs unreadable") });
    await open();

    expect(await screen.findByRole("alert")).toHaveTextContent("Handoffs unreadable");

    api.set("/api/v1/handoffs", ok(handoffs, { total: 3, limit: SIZE, offset: 0 }));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("task-0107")).toBeInTheDocument();
  });

  it("has no accessibility violation in the data state", async () => {
    mockApi({ "/api/v1/handoffs": pagedHandler });
    const { container } = await open();
    await screen.findByText("task-160");

    await expectNoAxeViolations(container);
  });

  it("links to the handoffs documentation in a new tab", async () => {
    mockApi({ "/api/v1/handoffs": ok(handoffs, { total: 3, limit: SIZE, offset: 0 }) });
    await open();

    const link = await screen.findByRole("link", { name: /Learn more/ });

    expect(link).toHaveAttribute("href", `${DOCS_BLOB_URL}/${docsLinks.handoffs.path}#${docsLinks.handoffs.anchor}`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DOCS_BLOB_URL } from "@/config/constants";
import docsLinks from "@/config/docs-links.json";
import { ROUTES } from "@/config/routes";
import handoffFixture from "@/test/fixtures/harness/handoff.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";
import type { HandoffDoc } from "@/types/api";

import { HandoffPage } from "./HandoffPage";

const invalid = handoffFixture.body.data as HandoffDoc;
const valid: HandoffDoc = { ...invalid, task: "task-0106", valid: true, problems: [] };

async function open(task = "task-0107") {
  return renderWithApp(<HandoffPage />, { url: `/handoffs/${task}`, path: ROUTES.handoff });
}

describe("Handoff reader", () => {
  it("renders the body as markdown and lists the problems of an invalid document", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": ok(invalid) });
    await open();

    expect(await screen.findByRole("heading", { level: 1, name: "Handoff task-0107" })).toBeInTheDocument();
    expect(screen.getByText("Invalid")).toBeInTheDocument();
    expect(screen.getByText("Run", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("verify", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("python -m unittest", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText("first item")).toBeInTheDocument();

    const problems = screen.getByRole("heading", { name: "Problems found by the validator" }).closest("div") as HTMLElement;
    expect(within(problems).getAllByRole("listitem").map((item) => item.textContent)).toEqual(invalid.problems);
  });

  it("turns a task id in the text into a link to the task", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": ok(invalid) });
    await open();

    const links = await screen.findAllByRole("link", { name: "task-0106" });

    expect(links.some((link) => link.getAttribute("href") === "/tasks/task-0106")).toBe(true);
  });

  it("shows no problems block for a valid document", async () => {
    mockApi({ "/api/v1/handoffs/task-0106": ok(valid) });
    await open("task-0106");

    expect(await screen.findByText("Valid")).toBeInTheDocument();
    expect(screen.queryByText("Problems found by the validator")).not.toBeInTheDocument();
  });

  it("shows script and image markup in the body as inert text, never as elements", async () => {
    const hostile: HandoffDoc = {
      ...valid,
      body: "# Title\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[click](javascript:alert(1))\n",
    };
    mockApi({ "/api/v1/handoffs/task-0106": ok(hostile) });
    const { container } = await open("task-0106");

    await screen.findByRole("heading", { level: 1, name: "Handoff task-0106" });

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(container).toHaveTextContent("<script>alert(1)</script>");
  });

  it("links back to the list", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": ok(invalid) });
    await open();

    expect(await screen.findByRole("link", { name: "All handoffs" })).toHaveAttribute("href", "/handoffs");
  });
});

describe("Handoff reader states", () => {
  it("shows a skeleton while loading", async () => {
    const api = mockApi();
    api.fetch.mockImplementation(() => new Promise<Response>(() => undefined));
    await open();

    expect(screen.getByRole("status", { name: "Loading the handoff" })).toBeInTheDocument();
  });

  it("says the document has no text when the body is blank", async () => {
    mockApi({ "/api/v1/handoffs/task-0106": ok({ ...valid, body: "  \n" }) });
    await open("task-0106");

    expect(await screen.findByText("This document has no text.")).toBeInTheDocument();
  });

  it("shows the not-found page for an unknown id", async () => {
    mockApi({ "/api/v1/handoffs/task-9999": fail(404, "not_found", "Not found") });
    await open("task-9999");

    expect(await screen.findByRole("heading", { name: "Handoff not found" })).toBeInTheDocument();
    expect(screen.getByText("There is no handoff document for task-9999.")).toBeInTheDocument();
  });

  it("shows the server message and retries on click", async () => {
    const api = mockApi({ "/api/v1/handoffs/task-0107": fail(500, "internal", "Reader failed") });
    await open();

    expect(await screen.findByRole("alert")).toHaveTextContent("Reader failed");

    api.set("/api/v1/handoffs/task-0107", ok(invalid));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Handoff task-0107" })).toBeInTheDocument();
  });

  it("has no accessibility violation in the data state", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": ok(invalid) });
    const { container } = await open();
    await screen.findByRole("heading", { level: 1, name: "Handoff task-0107" });

    await expectNoAxeViolations(container);
  });

  it("links to the handoff documentation in a new tab", async () => {
    mockApi({ "/api/v1/handoffs/task-0107": ok(invalid) });
    await open();

    const link = await screen.findByRole("link", { name: /Learn more/ });

    expect(link).toHaveAttribute("href", `${DOCS_BLOB_URL}/${docsLinks.handoff.path}#${docsLinks.handoff.anchor}`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

import { screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DOCS_URL } from "@/config/constants";
import { NAV_ENTRIES } from "@/config/nav";
import { startWsClient } from "@/lib/ws-client";
import runsFixture from "@/test/fixtures/shell/runs.json";
import statusFixture from "@/test/fixtures/shell/status.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, networkFailure, ok } from "@/test/utils/api-mock";
import { FakeWebSocket, helloMessage, installFakeWebSocket } from "@/test/utils/fake-ws";
import { renderWithApp } from "@/test/utils/render";

const status = statusFixture.body.data;
const runs = runsFixture.body.data;

let stop: (() => void) | undefined;

beforeEach(() => {
  installFakeWebSocket();
});

afterEach(() => {
  stop?.();
  stop = undefined;
});

async function renderShell() {
  mockApi({ "/api/v1/status": ok(status), "/api/v1/runs": ok(runs) });
  return renderWithApp(<p>page body</p>, { shell: true });
}

describe("shell navigation", () => {
  it("lists the ten nav entries in order", async () => {
    await renderShell();

    const nav = screen.getByRole("navigation", { name: "Views" });
    const labels = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent?.replace(/\d+$/, "").trim());
    expect(labels).toEqual([
      "Overview",
      "Needs you",
      "Board",
      "Tasks",
      "Activity",
      "Metrics",
      "Memory",
      "Graphs",
      "Harness",
      "Handoffs",
    ]);
    expect(NAV_ENTRIES).toHaveLength(10);
  });

  it("marks the current section with aria-current=page", async () => {
    await renderShell();

    const overview = within(screen.getByRole("navigation", { name: "Views" })).getByRole("link", { name: /Overview/ });
    expect(overview).toHaveAttribute("aria-current", "page");
  });

  it("shows the number of runs waiting for the user on the Needs you entry", async () => {
    await renderShell();

    expect(await screen.findByLabelText("1 waiting for you")).toBeInTheDocument();
  });

  it("shows no count when nothing is waiting", async () => {
    mockApi({ "/api/v1/status": ok(status), "/api/v1/runs": ok([]) });
    await renderWithApp(<p>page body</p>, { shell: true });

    await screen.findByText("v0.2.0");
    expect(screen.queryByLabelText(/waiting for you/)).not.toBeInTheDocument();
  });

  it("links Docs to the GitHub documentation in a new tab", async () => {
    await renderShell();

    const docs = screen.getByRole("link", { name: /^Docs/ });
    expect(docs).toHaveAttribute("href", "https://github.com/AlexWaha/agentry/tree/main/docs");
    expect(docs).toHaveAttribute("href", DOCS_URL);
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("shell chrome", () => {
  it("shows the logo beside the product name, the theme switch and the Read-only tag", async () => {
    await renderShell();

    expect(screen.getAllByText("Agentry").length).toBeGreaterThan(0);
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByText("Read-only")).toBeInTheDocument();
  });

  it("shows the plugin version, project and dials from /status", async () => {
    await renderShell();

    expect(await screen.findByText("v0.2.0")).toBeInTheDocument();
    expect(screen.getByText("demo-project")).toBeInTheDocument();
    const dials = screen.getByText("Approvals").closest("dl") as HTMLElement;
    expect(within(dials).getByText("manual")).toBeInTheDocument();
    expect(within(dials).getByText("solo")).toBeInTheDocument();
    expect(within(dials).getByText("Off")).toBeInTheDocument();
  });

  it("renders the page outlet content", async () => {
    await renderShell();

    expect(screen.getByRole("main")).toHaveTextContent("page body");
  });

  it("has no axe violations", async () => {
    const { container } = await renderShell();
    await screen.findByText("v0.2.0");

    await expectNoAxeViolations(container);
  });

  it("reaches the skip link, the nav entries, the Docs link and the theme switch with Tab", async () => {
    await renderShell();
    const user = userEvent.setup();
    const reached: string[] = [];

    for (let step = 0; step < 16; step += 1) {
      await user.tab();
      const element = document.activeElement as HTMLElement;
      reached.push(element.textContent?.trim() || element.getAttribute("value") || element.tagName);
    }

    expect(reached[0]).toBe("Skip to content");
    for (const label of ["Overview", "Needs you", "Handoffs"]) expect(reached.some((text) => text.startsWith(label))).toBe(true);
    expect(reached.some((text) => text.startsWith("Docs"))).toBe(true);
    expect(reached).toContain("system");
  });
});

describe("connection indicator", () => {
  it("reads Reconnecting before hello, Live after it", async () => {
    mockApi({ "/api/v1/status": ok(status), "/api/v1/runs": ok(runs) });
    const { queryClient } = await renderWithApp(<p>page body</p>, { shell: true });
    stop = startWsClient(queryClient);

    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
    FakeWebSocket.last.open();
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");

    FakeWebSocket.last.receive(helloMessage);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Live"));
  });

  it("reads Offline when /status fails with a network error", async () => {
    mockApi({ "/api/v1/status": networkFailure(), "/api/v1/runs": ok([]) });
    await renderWithApp(<p>page body</p>, { shell: true });

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Offline"));
  });
});

describe("expired link", () => {
  it("replaces the page with the expired-link page when the API answers 401", async () => {
    mockApi({ "/api/v1/status": fail(401, "unauthorized"), "/api/v1/runs": fail(401, "unauthorized") });
    await renderWithApp(<p>page body</p>, { shell: true });

    expect(await screen.findByText(/Run \/agentry:dashboard again/)).toBeInTheDocument();
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("This dashboard link has expired");
  });
});

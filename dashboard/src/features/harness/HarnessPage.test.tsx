import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DOCS_BLOB_URL } from "@/config/constants";
import docsLinks from "@/config/docs-links.json";
import { ROUTES } from "@/config/routes";
import harnessFixture from "@/test/fixtures/harness/harness.json";
import statusFixture from "@/test/fixtures/harness/status.json";
import { expectNoAxeViolations } from "@/test/utils/a11y";
import { fail, mockApi, ok } from "@/test/utils/api-mock";
import { renderWithApp } from "@/test/utils/render";
import type { Harness, Status } from "@/types/api";

import { HarnessPage } from "./HarnessPage";
import { splitMatcher, validateHarnessSearch } from "./search";

const harness = harnessFixture.body.data as Harness;
const status = statusFixture.body.data as Status;
const LONG_MATCHER = harness.hooks[2].matcher;

const empty: Harness = { plugin: harness.plugin, agents: [], rules: [], skills: [], hooks: [] };

async function open(url = "/harness", data: Harness = harness) {
  const api = mockApi({ "/api/v1/harness": ok(data), "/api/v1/status": ok(status) });
  const app = await renderWithApp(<HarnessPage />, { url, path: ROUTES.harness, validateSearch: validateHarnessSearch });
  return { api, ...app };
}

describe("Harness tabs", () => {
  it("shows the Agents tab without a tab param, with the counts in the tab labels and one panel", async () => {
    await open();

    expect(await screen.findByRole("tab", { name: "Agents 4", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Harness section" })).toBeInTheDocument();
    for (const name of ["Rules 6", "Skills 3", "Hooks 4", "Plugin"]) {
      expect(screen.getByRole("tab", { name, selected: false })).toBeInTheDocument();
    }
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Technical" })).toBeInTheDocument();
  });

  it("falls back to Agents for an unknown tab value", async () => {
    await open("/harness?tab=bogus");

    expect(await screen.findByRole("tab", { name: "Agents 4", selected: true })).toBeInTheDocument();
  });

  it("opens the tab named in the URL", async () => {
    await open("/harness?tab=plugin");

    expect(await screen.findByRole("tab", { name: "Plugin", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime" })).toBeInTheDocument();
  });

  it("writes the clicked tab into the URL, shows only its panel, and drops the param for Agents", async () => {
    const { router } = await open();

    await userEvent.click(await screen.findByRole("tab", { name: "Hooks 4" }));

    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "hooks" }));
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "PreToolUse" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Technical" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Agents 4" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.getByRole("heading", { name: "Technical" })).toBeInTheDocument();
  });

  it("moves focus and selection to the next tab on ArrowRight", async () => {
    await open();
    (await screen.findByRole("tab", { name: "Agents 4" })).focus();

    await userEvent.keyboard("{ArrowRight}");

    await waitFor(() => expect(screen.getByRole("tab", { name: "Rules 6" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("tab", { name: "Rules 6" })).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Core" })).toBeInTheDocument();
  });
});

describe("Agents tab", () => {
  it("groups agents by department with profile, model, rules and skills", async () => {
    await open();

    const technical = (await screen.findByRole("heading", { name: "Technical" })).closest("div")?.parentElement as HTMLElement;
    expect(within(technical).getByText("architect")).toBeInTheDocument();
    expect(within(technical).getByText("senior-backend-dev")).toBeInTheDocument();
    expect(within(technical).getByText("readonly")).toBeInTheDocument();
    expect(within(technical).getByText("opus")).toBeInTheDocument();
    expect(within(technical).getByText("2 skills, 2 rules")).toBeInTheDocument();
    expect(within(technical).getByText("coding-style.md")).toBeInTheDocument();
    expect(within(technical).getByText("feature-scaffold")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Support" })).toBeInTheDocument();
  });

  it("puts an agent without a department under Other, last", async () => {
    await open();

    const headings = (await screen.findAllByRole("heading", { level: 3 })).map((heading) => heading.textContent);

    expect(headings).toEqual(["Technical", "Support", "Other"]);
  });
});

describe("Rules tab", () => {
  it("groups rules by delivery with sizes, and names the agents of a targeted rule", async () => {
    await open("/harness?tab=rules");

    for (const group of ["Core", "Main thread", "Targeted", "Unused"]) {
      expect(await screen.findByRole("heading", { name: group })).toBeInTheDocument();
    }
    expect(screen.getByText("quality-standard.md")).toBeInTheDocument();
    expect(screen.getByText("18.0 KB")).toBeInTheDocument();
    expect(screen.getByText("512 B")).toBeInTheDocument();
    expect(screen.getByText("qa-engineer")).toBeInTheDocument();
  });
});

describe("Skills tab", () => {
  it("shows the slash command, source, user-only flag, description and who preloads it", async () => {
    await open("/harness?tab=skills");

    expect(await screen.findByText("/agentry:bug-fix")).toBeInTheDocument();
    expect(screen.getByText("/verify")).toBeInTheDocument();
    expect(screen.getByText("Structured RED-GREEN debugging workflow.")).toBeInTheDocument();
    expect(screen.getAllByText("User only")).toHaveLength(1);
    expect(screen.getByText("senior-frontend-dev")).toBeInTheDocument();
    expect(screen.getByText("project")).toBeInTheDocument();
  });
});

describe("Hooks tab", () => {
  it("groups hooks by event, flags the missing script once, and splits a matcher into chips", async () => {
    await open("/harness?tab=hooks");

    expect(await screen.findByRole("heading", { name: "SessionStart" })).toBeInTheDocument();
    expect(screen.getByText("tools/pipeline/pretool_gate.py")).toBeInTheDocument();
    expect(screen.getAllByText("Script missing")).toHaveLength(1);
    expect(screen.getByText("60s timeout")).toBeInTheDocument();
    for (const part of ["Edit", "Write", "MultiEdit"]) expect(screen.getByText(part)).toBeInTheDocument();
    expect(screen.getByText("echo done")).toBeInTheDocument();
  });

  it("wraps a 120-character unbroken matcher and never truncates it", async () => {
    await open("/harness?tab=hooks");

    const chip = await screen.findByText(LONG_MATCHER);

    expect(LONG_MATCHER).toHaveLength(120);
    expect(chip).toHaveClass("wrap-anywhere");
    expect(chip).not.toHaveClass("truncate");
    expect(chip).not.toHaveAttribute("title");
  });

  it("splits a matcher only at top-level alternatives", () => {
    expect(splitMatcher("Edit|Write|(a|b)")).toEqual(["Edit", "Write", "(a|b)"]);
    expect(splitMatcher("")).toEqual([]);
  });
});

describe("Plugin tab and Runtime block", () => {
  it("shows name, version, source and root path", async () => {
    await open("/harness?tab=plugin");

    expect(await screen.findByText("agentry")).toBeInTheDocument();
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getByText("Directory (loaded in place)")).toBeInTheDocument();
    expect(screen.getByText("C:/Users/dev/.claude/plugins/agentry")).toBeInTheDocument();
  });

  it("shows the dials of each lane, worktrees, supervisor and one row per debt entry at the bottom", async () => {
    await open("/harness?tab=plugin");

    const runtime = (await screen.findByRole("heading", { name: "Runtime" })).closest("section") as HTMLElement;
    expect(await within(runtime).findByText("default")).toBeInTheDocument();
    expect(within(runtime).getByText("fix")).toBeInTheDocument();
    expect(within(runtime).getByText("plan")).toBeInTheDocument();
    expect(within(runtime).getByText("auto")).toBeInTheDocument();
    expect(within(runtime).getByText("On, max parallel 3")).toBeInTheDocument();
    expect(within(runtime).getByText("running, 2 runs")).toBeInTheDocument();
    expect(within(runtime).getByText("solo, push needs approval: On")).toBeInTheDocument();
    expect(within(runtime).getByRole("heading", { name: "Handoff documents owed (2)" })).toBeInTheDocument();
    expect(within(runtime).getByRole("heading", { name: "Memory reviews owed (1)" })).toBeInTheDocument();
    expect(within(runtime).getAllByRole("link", { name: "task-0108" })).toHaveLength(2);
    expect(within(runtime).getByRole("link", { name: "task-0109" })).toBeInTheDocument();
    expect(within(runtime).getByText("memory layers not reviewed after completion")).toBeInTheDocument();
    expect(runtime.compareDocumentPosition(screen.getByText("Root path")) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it("says nothing is owed when there is no debt", async () => {
    const api = mockApi({
      "/api/v1/harness": ok(harness),
      "/api/v1/status": ok({ ...status, debt: { handoffs: [], memory: [] } }),
    });
    await renderWithApp(<HarnessPage />, { url: "/harness?tab=plugin", path: ROUTES.harness, validateSearch: validateHarnessSearch });

    expect(await screen.findAllByText("Nothing owed.")).toHaveLength(2);
    expect(api.callsTo("/api/v1/status")).toHaveLength(1);
  });

  it("shows an error with Retry when the runtime state cannot be read", async () => {
    const api = mockApi({ "/api/v1/harness": ok(harness), "/api/v1/status": fail(500, "internal", "Status failed") });
    await renderWithApp(<HarnessPage />, { url: "/harness?tab=plugin", path: ROUTES.harness, validateSearch: validateHarnessSearch });

    expect(await screen.findByRole("alert")).toHaveTextContent("Status failed");

    api.set("/api/v1/status", ok(status));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("running, 2 runs")).toBeInTheDocument();
  });
});

describe("Harness page states", () => {
  it("shows a skeleton while loading, with the page header and docs link already there", async () => {
    const api = mockApi();
    api.fetch.mockImplementation(() => new Promise<Response>(() => undefined));
    await renderWithApp(<HarnessPage />, { url: "/harness", path: ROUTES.harness, validateSearch: validateHarnessSearch });

    expect(screen.getByRole("status", { name: "Loading the harness" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Harness" })).toBeInTheDocument();
  });

  it("says why a tab is empty", async () => {
    await open("/harness", empty);

    expect(await screen.findByText("No agents found.")).toBeInTheDocument();
    expect(screen.getByText(/no .claude\/agents folder/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Hooks 0" }));

    expect(await screen.findByText("No hooks found.")).toBeInTheDocument();
  });

  it("shows the server message and retries on click", async () => {
    const api = mockApi({ "/api/v1/harness": fail(500, "internal", "Harness unreadable") });
    await renderWithApp(<HarnessPage />, { url: "/harness", path: ROUTES.harness, validateSearch: validateHarnessSearch });

    expect(await screen.findByRole("alert")).toHaveTextContent("Harness unreadable");

    api.set("/api/v1/harness", ok(harness));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("tab", { name: "Agents 4" })).toBeInTheDocument();
  });

  it.each(["agents", "rules", "skills", "hooks", "plugin"])("has no accessibility violation on the %s tab", async (tab) => {
    const { container } = await open(`/harness?tab=${tab}`);
    await screen.findByRole("tabpanel");
    if (tab === "plugin") await screen.findByText("default");

    await expectNoAxeViolations(container);
  });

  it("links to the harness documentation in a new tab", async () => {
    await open();

    const link = await screen.findByRole("link", { name: /Learn more/ });

    expect(link).toHaveAttribute("href", `${DOCS_BLOB_URL}/${docsLinks.harness.path}`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

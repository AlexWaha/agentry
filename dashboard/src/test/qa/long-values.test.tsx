import { describe, expect, it } from "vitest";

import { StatusChip } from "@/components/shared/StatusChip";
import { Chip } from "@/components/shared/Chip";
import { render, screen } from "@testing-library/react";

import { renderApp, settle } from "./app";
import { findOverflowRisks, LONG_LENGTH, LONG_ROUTES, longValue } from "./long-values";

/*
 * QA: a 120-character value without a space in every free-text field must not push a container wider than the
 * page (FR-128, FR-90). jsdom has no layout, so each value is judged by the classes that decide it: a wrapping
 * class on the element or an ancestor, an ellipsis class with the full text in a title, or its own scroll.
 */

/** The pages whose fields all wrap or ellipsize correctly. */
const CLEAN_PAGES = [
  "/",
  "/needs",
  "/board",
  "/tasks",
  "/tasks/task-0104",
  "/activity",
  "/metrics",
  "/memory",
  "/memory?tab=pattern",
  "/memory?tab=module",
  "/memory?tab=stats",
  "/graphs?view=code",
  "/harness?tab=rules",
  "/harness?tab=hooks",
  "/harness?tab=plugin",
];

describe("long unbroken values", () => {
  it.each(CLEAN_PAGES)("%s holds every 120-character field", async (page) => {
    await renderApp(page, { routes: LONG_ROUTES });
    await settle(250);

    expect(screen.getByRole("main").textContent).toContain("W".repeat(LONG_LENGTH - 20));
    expect(findOverflowRisks()).toEqual([]);
  });
});

describe("Chip", () => {
  it("ellipsizes a long string and carries the full text in its title (FR-128)", () => {
    const text = longValue("epic");
    render(<Chip>{text}</Chip>);

    const chip = screen.getByText(text);
    expect(chip).toHaveClass("truncate", "max-w-full", "min-w-0");
    expect(chip).toHaveAttribute("title", text);
  });

  it("a chip that must stay readable wraps and has no title-only truncation", () => {
    const text = longValue("matcher");
    render(<Chip wrap>{text}</Chip>);

    const chip = screen.getByText(text);
    expect(chip).toHaveClass("wrap-anywhere");
    expect(chip).not.toHaveClass("truncate");
    expect(chip).not.toHaveAttribute("title");
  });
});

describe("StatusChip", () => {
  it("never grows beyond its container", () => {
    render(<StatusChip status="active" label={longValue("label")} />);

    expect(document.querySelector("[data-status]")).toHaveClass("max-w-full");
  });
});

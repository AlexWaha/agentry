import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { TabPanel, Tabs, type TabItem } from "@/components/shared/Tabs";

type Tab = "agents" | "rules" | "skills";

const tabs: TabItem<Tab>[] = [
  { value: "agents", label: "Agents", count: 31 },
  { value: "rules", label: "Rules", count: 12 },
  { value: "skills", label: "Skills", count: 40 },
];

function Harness({ initial = "agents" as Tab }) {
  const [value, setValue] = useState<Tab>(initial);
  return (
    <>
      <Tabs tabs={tabs} value={value} onChange={setValue} label="Harness sections" idBase="h" />
      <TabPanel idBase="h" value={value}>
        panel {value}
      </TabPanel>
    </>
  );
}

describe("Tabs", () => {
  it("renders a tablist with the count after each label and aria-selected on the current tab", () => {
    render(<Harness />);

    expect(screen.getByRole("tablist", { name: "Harness sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Agents 31" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Rules 12" })).toHaveAttribute("aria-selected", "false");
  });

  it("links each tab to its panel", () => {
    render(<Harness />);

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("panel agents");
    expect(panel).toHaveAttribute("aria-labelledby", screen.getByRole("tab", { name: "Agents 31" }).id);
  });

  it("selects a tab on click and renders only its panel", async () => {
    render(<Harness />);

    await userEvent.click(screen.getByRole("tab", { name: "Rules 12" }));

    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel rules");
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  });

  it("moves with the arrow keys, wrapping at both ends, and moves focus with the selection", async () => {
    render(<Harness />);
    screen.getByRole("tab", { name: "Agents 31" }).focus();

    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Rules 12" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Rules 12" })).toHaveAttribute("aria-selected", "true");

    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Skills 40" })).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel skills");
  });

  it("jumps to the first and last tab with Home and End", async () => {
    render(<Harness initial="rules" />);
    screen.getByRole("tab", { name: "Rules 12" }).focus();

    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Skills 40" })).toHaveAttribute("aria-selected", "true");

    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Agents 31" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps only the selected tab in the Tab order", () => {
    render(<Harness />);

    expect(screen.getByRole("tab", { name: "Agents 31" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Rules 12" })).toHaveAttribute("tabindex", "-1");
  });

  it("scrolls the bar inside itself on narrow widths", () => {
    const { container } = render(<Harness />);

    expect(container.firstElementChild).toHaveClass("overflow-x-auto");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusChip, statusOfFolder } from "@/components/shared/StatusChip";

describe("StatusChip", () => {
  it.each([
    ["backlog", "Backlog"],
    ["active", "Active"],
    ["done", "Done"],
    ["need", "Needs you"],
  ] as const)("shows the text label %s so status is never color alone", (status, label) => {
    render(<StatusChip status={status} />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(label).closest("[data-status]")).toHaveAttribute("data-status", status);
  });

  it("uses the palette of its status: gray, blue, green, and the attention role only for need", () => {
    const { container } = render(
      <>
        <StatusChip status="backlog" />
        <StatusChip status="active" />
        <StatusChip status="done" />
        <StatusChip status="need" />
      </>,
    );
    const chips = container.querySelectorAll("[data-status]");

    expect(chips[0]).toHaveClass("text-status-backlog-fg");
    expect(chips[1]).toHaveClass("text-status-active-fg");
    expect(chips[2]).toHaveClass("text-status-done-fg");
    expect(chips[3]).toHaveClass("text-attention");
  });

  it("accepts a custom label and never grows beyond its container", () => {
    render(<StatusChip status="active" label="Implement" />);

    expect(screen.getByText("Implement").closest("[data-status]")).toHaveClass("max-w-full");
  });

  it("maps a folder to a status and treats an unknown folder as backlog", () => {
    expect(statusOfFolder("done")).toBe("done");
    expect(statusOfFolder("active")).toBe("active");
    expect(statusOfFolder("")).toBe("backlog");
  });
});

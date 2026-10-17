import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip, ChipRow } from "@/components/shared/Chip";

const LONG_VALUE = "x".repeat(120);

describe("Chip", () => {
  it("ellipsizes a 120-character unbroken value and carries the full text in its title", () => {
    render(<Chip>{LONG_VALUE}</Chip>);

    const chip = screen.getByText(LONG_VALUE);
    expect(chip).toHaveClass("truncate");
    expect(chip).toHaveClass("max-w-full");
    expect(chip).toHaveAttribute("title", LONG_VALUE);
  });

  it("wraps a hook matcher instead of cutting it, with no ellipsis class and no truncation", () => {
    render(<Chip wrap>{LONG_VALUE}</Chip>);

    const chip = screen.getByText(LONG_VALUE);
    expect(chip).toHaveClass("wrap-anywhere");
    expect(chip).not.toHaveClass("truncate");
    expect(chip).not.toHaveAttribute("title");
  });

  it("lets a memory signature wrap the same way", () => {
    render(<Chip wrap title="signature">{LONG_VALUE}</Chip>);

    expect(screen.getByText(LONG_VALUE)).toHaveClass("wrap-anywhere");
  });

  it("keeps an explicit title over the text", () => {
    render(<Chip title="Epic: dashboard">epic-0005</Chip>);

    expect(screen.getByText("epic-0005")).toHaveAttribute("title", "Epic: dashboard");
  });

  it("puts chips in a row that wraps", () => {
    render(
      <ChipRow data-testid="row">
        <Chip>a</Chip>
        <Chip>b</Chip>
      </ChipRow>,
    );

    expect(screen.getByTestId("row")).toHaveClass("flex-wrap");
  });
});

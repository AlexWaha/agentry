import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { GraphNode } from "@/types/api";

import { GraphTable } from "./GraphTable";

const nodesOf = (count: number): GraphNode[] =>
  Array.from({ length: count }, (_, index) => ({ id: `n${index}`, kind: "lesson", label: `n${index}`, weight: 1 }));

describe("GraphTable paging", () => {
  it("falls back to the last page, with its pager, when the graph shrinks under the current page", async () => {
    const { rerender } = render(<GraphTable nodes={nodesOf(250)} edges={[]} />);
    await userEvent.click(screen.getByRole("button", { name: "Page 3" }));
    expect(screen.getByRole("status")).toHaveTextContent("201-250 of 250 nodes");

    rerender(<GraphTable nodes={nodesOf(120)} edges={[]} />);

    expect(screen.getByRole("status")).toHaveTextContent("101-120 of 120 nodes");
    expect(within(screen.getByRole("table", { name: "Nodes" })).getAllByRole("row")).toHaveLength(21);
  });
});

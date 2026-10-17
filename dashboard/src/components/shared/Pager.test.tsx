import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pager, pageWindow } from "@/components/shared/Pager";

describe("Pager", () => {
  it("shows the range text and the numbered pages for page 2 of 5", () => {
    render(<Pager page={2} size={25} total={120} noun="tasks" onPage={vi.fn()} />);

    expect(screen.getByText("26-50 of 120 tasks")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Page \d$/ })).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Page 3" })).not.toHaveAttribute("aria-current");
  });

  it("clips the last range to the total", () => {
    render(<Pager page={5} size={25} total={120} noun="tasks" onPage={vi.fn()} />);

    expect(screen.getByText("101-120 of 120 tasks")).toBeInTheDocument();
  });

  it("disables Previous on the first page and Next on the last page", () => {
    const { rerender } = render(<Pager page={1} size={25} total={120} noun="tasks" onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Next/ })).toBeEnabled();

    rerender(<Pager page={5} size={25} total={120} noun="tasks" onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Previous/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
  });

  it("reports the clicked page through onPage", async () => {
    const onPage = vi.fn();
    render(<Pager page={2} size={25} total={120} noun="tasks" onPage={onPage} />);

    await userEvent.click(screen.getByRole("button", { name: "Page 4" }));
    await userEvent.click(screen.getByRole("button", { name: /Next/ }));
    await userEvent.click(screen.getByRole("button", { name: /Previous/ }));

    expect(onPage.mock.calls).toEqual([[4], [3], [1]]);
  });

  it("collapses runs of pages into ellipses at 10 pages", () => {
    render(<Pager page={5} size={25} total={250} noun="tasks" onPage={vi.fn()} />);

    const pages = screen.getAllByRole("button", { name: /^Page \d+$/ }).map((button) => button.textContent);
    expect(pages).toEqual(["1", "4", "5", "6", "10"]);
    const nav = screen.getByRole("navigation");
    expect(within(nav).getAllByText("...")).toHaveLength(2);
  });

  it("renders nothing when the list is empty", () => {
    const { container } = render(<Pager page={1} size={25} total={0} noun="tasks" onPage={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("shows only the count when everything fits on one page", () => {
    render(<Pager page={1} size={25} total={7} noun="events" onPage={vi.fn()} />);

    expect(screen.getByText("1-7 of 7 events")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("wraps its content instead of overflowing on narrow widths", () => {
    render(<Pager page={1} size={25} total={120} noun="tasks" onPage={vi.fn()} />);

    expect(screen.getByRole("navigation")).toHaveClass("flex-wrap");
  });
});

describe("pageWindow", () => {
  it("lists every page up to seven pages", () => {
    expect(pageWindow(3, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("keeps the first, last and current neighbours beyond seven pages", () => {
    expect(pageWindow(1, 9)).toEqual([1, 2, null, 9]);
    expect(pageWindow(5, 9)).toEqual([1, null, 4, 5, 6, null, 9]);
    expect(pageWindow(9, 9)).toEqual([1, null, 8, 9]);
  });
});

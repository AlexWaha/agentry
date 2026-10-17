import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Pager } from "@/components/shared/Pager";
import {
  clampPage,
  enumParam,
  flagParam,
  hasFilters,
  lastPage,
  pageOffset,
  pageParam,
  parsePage,
  textParam,
  useClampPage,
  useListSearch,
  useSearchText,
  withFilters,
  withPage,
} from "@/lib/search-params";
import { renderWithApp } from "@/test/utils/render";

const FOLDERS = ["backlog", "active", "done"] as const;
const SIZE = 25;

interface TestSearch {
  page?: number;
  folder?: (typeof FOLDERS)[number];
  q?: string;
}

const validateSearch = (raw: Record<string, unknown>): TestSearch => ({
  page: pageParam(raw.page),
  folder: enumParam(raw.folder, FOLDERS),
  q: textParam(raw.q),
});

function TestList({ total }: { total: number }) {
  const { search, page, setPage, setFilters, clearFilters } = useListSearch<TestSearch>();
  useClampPage(page, total, SIZE, setPage);
  const [text, setText] = useSearchText(search.q ?? "", (value) => setFilters({ q: value || undefined }, { replace: true }));
  return (
    <div>
      <input aria-label="search" value={text} onChange={(event) => setText(event.target.value)} />
      <button onClick={() => setFilters({ folder: "done" })}>pick done</button>
      <button onClick={() => clearFilters(["folder", "q"])}>clear</button>
      <output data-testid="page">{clampPage(page, total, SIZE)}</output>
      <Pager page={clampPage(page, total, SIZE)} size={SIZE} total={total} noun="tasks" onPage={setPage} />
    </div>
  );
}

async function renderList(url: string, total = 120) {
  return renderWithApp(<TestList total={total} />, { url, path: "/tasks", validateSearch });
}

describe("page and filter restore from the URL", () => {
  it("restores ?page=2&folder=done", async () => {
    const { router } = await renderList("/tasks?page=2&folder=done");

    expect(screen.getByTestId("page")).toHaveTextContent("2");
    expect(screen.getByText("26-50 of 120 tasks")).toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({ page: 2, folder: "done" });
  });

  it("drops page when a filter changes", async () => {
    const { router } = await renderList("/tasks?page=3");

    await userEvent.click(screen.getByRole("button", { name: "pick done" }));

    await waitFor(() => expect(router.state.location.search).toEqual({ folder: "done" }));
    expect(screen.getByTestId("page")).toHaveTextContent("1");
  });

  it("omits page from the URL when going back to page 1", async () => {
    const { router } = await renderList("/tasks?page=2");

    await userEvent.click(screen.getByRole("button", { name: "Page 1" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("writes the page to the URL when a page link is clicked", async () => {
    const { router } = await renderList("/tasks");

    await userEvent.click(screen.getByRole("button", { name: "Page 4" }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 4 }));
  });

  it("renders the last page and rewrites the URL for ?page=99", async () => {
    const { router } = await renderList("/tasks?page=99");

    expect(screen.getByTestId("page")).toHaveTextContent("5");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 5 }));
    expect(router.history.length).toBe(1);
  });

  it.each(["abc", "0", "-3", "1.5"])("treats ?page=%s as page 1", async (raw) => {
    await renderList(`/tasks?page=${raw}`);

    expect(screen.getByTestId("page")).toHaveTextContent("1");
    expect(screen.getByText("1-25 of 120 tasks")).toBeInTheDocument();
  });

  it("hides the pager when the list is empty", async () => {
    await renderList("/tasks", 0);

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("clears the named filters and the page", async () => {
    const { router } = await renderList("/tasks?folder=done&page=2&q=abc");

    await userEvent.click(screen.getByRole("button", { name: "clear" }));

    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.getByRole("textbox", { name: "search" })).toHaveValue("");
  });
});

describe("search box", () => {
  it("commits after the quiet period without adding history entries", async () => {
    const { router } = await renderList("/tasks?page=3");
    const lengthBefore = router.history.length;

    await userEvent.type(screen.getByRole("textbox", { name: "search" }), "abc");
    expect(router.state.location.search).toMatchObject({ page: 3 });
    expect(router.state.location.search).not.toHaveProperty("q");

    await waitFor(() => expect(router.state.location.search).toEqual({ q: "abc" }), { timeout: 2000 });
    expect(router.history.length).toBe(lengthBefore);
  });

  it("shows the text of the URL on load", async () => {
    await renderList("/tasks?q=hello");

    expect(screen.getByRole("textbox", { name: "search" })).toHaveValue("hello");
  });
});

describe("search-param helpers", () => {
  it("parses a page: integers of at least 1, everything else is 1", () => {
    expect(parsePage(3)).toBe(3);
    expect(parsePage("4")).toBe(4);
    expect(parsePage(0)).toBe(1);
    expect(parsePage("abc")).toBe(1);
    expect(parsePage(undefined)).toBe(1);
    expect(parsePage(2.5)).toBe(1);
  });

  it("keeps a page param only when it is 2 or more", () => {
    expect(pageParam(1)).toBeUndefined();
    expect(pageParam("junk")).toBeUndefined();
    expect(pageParam(2)).toBe(2);
  });

  it("accepts text, numbers and rejects blanks", () => {
    expect(textParam("  ")).toBeUndefined();
    expect(textParam(123)).toBe("123");
    expect(textParam("task-1")).toBe("task-1");
    expect(textParam({})).toBeUndefined();
  });

  it("keeps an enum value only when it is allowed", () => {
    expect(enumParam("done", FOLDERS)).toBe("done");
    expect(enumParam("nope", FOLDERS)).toBeUndefined();
  });

  it("reads 1 and true as a set flag", () => {
    expect(flagParam(1)).toBe(true);
    expect(flagParam("1")).toBe(true);
    expect(flagParam(0)).toBeUndefined();
    expect(flagParam(undefined)).toBeUndefined();
  });

  it("does the page arithmetic", () => {
    expect(lastPage(120, 25)).toBe(5);
    expect(lastPage(0, 25)).toBe(1);
    expect(pageOffset(3, 25)).toBe(50);
    expect(clampPage(99, 120, 25)).toBe(5);
    expect(clampPage(2, 120, 25)).toBe(2);
  });

  it("builds search objects: page omitted at 1, filter change resets the page, empties dropped", () => {
    expect(withPage<TestSearch>({ folder: "done" }, 1)).toEqual({ folder: "done" });
    expect(withPage<TestSearch>({ folder: "done" }, 3)).toEqual({ folder: "done", page: 3 });
    expect(withFilters<TestSearch>({ page: 4, q: "a" }, { q: "b" })).toEqual({ q: "b" });
    expect(withFilters<TestSearch>({ page: 4, q: "a" }, { q: undefined })).toEqual({});
    expect(hasFilters({ q: "x" }, ["folder", "q"])).toBe(true);
    expect(hasFilters({ page: 2 }, ["folder", "q"])).toBe(false);
  });
});

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown, renderMarkdown, safeLink, splitSections } from "@/lib/markdown";
import { renderWithApp } from "@/test/utils/render";

async function renderMd(source: string) {
  return renderWithApp(<Markdown source={source} />);
}

describe("Markdown XSS vectors", () => {
  const vectors: [string, string][] = [
    ["script tag", "Before <script>alert(1)</script> after"],
    ["img onerror", 'Look <img src="x" onerror="alert(1)"> here'],
    ["iframe", '<iframe src="javascript:alert(1)"></iframe>'],
    ["javascript link", "[click me](javascript:alert(1))"],
    ["mixed-case javascript link", "[click me](JaVaScRiPt:alert(1))"],
    ["data link", "[click me](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)"],
    ["vbscript link", "[click me](vbscript:msgbox(1))"],
    ["raw anchor html", '<a href="javascript:alert(1)" onclick="alert(2)">raw</a>'],
    ["inline handler", "<b onmouseover=alert(1)>bold</b>"],
    ["svg onload", "<svg onload=alert(1)></svg>"],
  ];

  it.each(vectors)("renders %s as inert text", async (_name, source) => {
    const { container } = await renderMd(source);

    expect(container.querySelector("script, img, iframe, svg, object, embed, style")).toBeNull();
    expect(container.querySelector('a[href^="javascript:" i], a[href^="data:" i], a[href^="vbscript:" i]')).toBeNull();
    expect(container.querySelector("[onerror], [onclick], [onload], [onmouseover]")).toBeNull();
  });

  it("shows the raw html as visible text", async () => {
    await renderMd("Before <script>alert(1)</script> after");

    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });

  it("keeps the label of a blocked link as plain text", async () => {
    const { container } = await renderMd("[click me](javascript:alert(1))");

    expect(screen.getByText("click me")).toBeInTheDocument();
    expect(container.querySelector("a")).toBeNull();
  });
});

describe("Markdown links", () => {
  it("opens an external link in a new tab with rel noopener noreferrer", async () => {
    await renderMd("[docs](https://github.com/AlexWaha/agentry)");

    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://github.com/AlexWaha/agentry");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("keeps a relative link in the page", async () => {
    await renderMd("[the board](/board)");

    const link = screen.getByRole("link", { name: "the board" });
    expect(link).toHaveAttribute("href", "/board");
    expect(link).not.toHaveAttribute("target");
  });

  it("turns a task id into a link to the task page", async () => {
    await renderMd("Depends on task-0140 and `task-0141`.");

    expect(screen.getByRole("link", { name: "task-0140" })).toHaveAttribute("href", "/tasks/task-0140");
    expect(screen.queryByRole("link", { name: "task-0141" })).not.toBeInTheDocument();
  });

  it("accepts only http, https and relative targets", () => {
    expect(safeLink("https://example.com/a")).toEqual({ href: "https://example.com/a", external: true });
    expect(safeLink("docs/x.md")).toEqual({ href: "docs/x.md", external: false });
    expect(safeLink("javascript:alert(1)")).toBeNull();
    expect(safeLink("data:text/plain,hi")).toBeNull();
    expect(safeLink("ftp://example.com/x")).toBeNull();
  });
});

describe("Markdown blocks", () => {
  it("renders task-list items as disabled checkboxes with the right state", async () => {
    await renderMd("- [x] first done\n- [ ] second open\n- [X] third done");

    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, false, true]);
    for (const box of boxes) expect(box).toBeDisabled();
  });

  it("counts checked and total task-list items", () => {
    const { checked, total } = renderMarkdown(["- [x] a", "- [ ] b", "  - [x] nested", "- plain item"]);

    expect(checked).toBe(2);
    expect(total).toBe(3);
  });

  it("renders headings, emphasis, inline code and a fenced block", async () => {
    await renderMd("### Heading\n\nSome **bold** and *italic* and `code`.\n\n```\nline one\nline two\n```");

    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("italic").tagName).toBe("EM");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getByText(/line one/).closest("pre")).not.toBeNull();
  });

  it("renders a table with a header row", async () => {
    await renderMd("| Name | Size |\n|---|---|\n| a | 1 |\n| b | 2 |");

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Name", "Size"]);
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("renders ordered and nested unordered lists", async () => {
    await renderMd("1. one\n2. two\n\n- top\n  - child");

    expect(screen.getAllByRole("list")).toHaveLength(3);
    expect(screen.getByText("child").closest("ul")).not.toBeNull();
  });

  it("does not crash on a separator-only table line or a lone pipe", async () => {
    const { container } = await renderMd("|---|---|\n\n|\n\n# \n\ntext");

    expect(container).toHaveTextContent("text");
  });

  it("keeps non-ASCII text and CRLF line endings intact", async () => {
    await renderMd("Line one\r\n\r\nSecond: café 中文");

    expect(screen.getByText(/café 中文/)).toBeInTheDocument();
  });
});

describe("splitSections", () => {
  it("splits at second-level headings and ignores headings inside fences", () => {
    const sections = splitSections("Intro\n\n## Description\ntext\n\n```\n## not a heading\n```\n\n## Notes\nmore");

    expect(sections.map((section) => section.title)).toEqual(["", "Description", "Notes"]);
    expect(sections[1].lines.join("\n")).toContain("## not a heading");
  });

  it("drops an empty leading section", () => {
    expect(splitSections("## Only\nbody").map((section) => section.title)).toEqual(["Only"]);
  });
});

describe("Markdown line parsing stays linear", () => {
  const SIZE = 30000;
  const SEPARATOR = "\u2028";
  const hostile: Record<string, string> = {
    "list item": `- ${" ".repeat(SIZE)}a${SEPARATOR}b`,
    "ordered list item": `1. ${" ".repeat(SIZE)}a${SEPARATOR}b`,
    "task-list item": `- [ ] ${" ".repeat(SIZE)}a${SEPARATOR}b`,
    heading: `### ${"x ".repeat(SIZE / 2)}${SEPARATOR}b`,
    "section heading": `## ${" ".repeat(SIZE)}a${SEPARATOR}b`,
    "indented text": `${" ".repeat(SIZE)}a${SEPARATOR}b`,
    "table row": `|${" ".repeat(SIZE)}a${SEPARATOR}b`,
    "block quote": `>${" ".repeat(SIZE)}a${SEPARATOR}b`,
    link: `[${"a".repeat(SIZE)}](${"b".repeat(SIZE)}${SEPARATOR}`,
  };

  it.each(Object.entries(hostile))("a %s with a stray line separator is parsed in under 100 ms", (_name, line) => {
    const start = performance.now();
    renderMarkdown([line]);
    splitSections(line);

    expect(performance.now() - start).toBeLessThan(100);
  });
});

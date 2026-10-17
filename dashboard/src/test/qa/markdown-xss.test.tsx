import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown, safeLink } from "@/lib/markdown";
import { renderWithApp } from "@/test/utils/render";
import detailFixture from "@/test/fixtures/pipeline/task-detail.json";
import handoffFixture from "@/test/fixtures/harness/handoff.json";

import { renderApp, settle } from "./app";

/*
 * QA: hostile markdown in task bodies and handoff documents (FR-87). The rule: nothing from the source may become
 * an executable element, an event handler attribute or a navigable dangerous URL; HTML stays visible as text.
 */

const VECTORS: [string, string][] = [
  ["a script element", "<script>alert(1)</script>"],
  ["an image with onerror", "<img src=x onerror=alert(1)>"],
  ["an svg with onload", "<svg onload=alert(1)>"],
  ["an iframe", "<iframe src=//evil.example></iframe>"],
  ["an object and an embed", "<object data=//evil.example></object><embed src=//evil.example>"],
  ["a style element", "<style>@import url(//evil.example/x.css)</style>"],
  ["a meta refresh and a base", '<meta http-equiv="refresh" content="0;url=//evil.example"><base href="//evil.example/">'],
  ["a form", '<form action="//evil.example"><input name=x></form>'],
  ["a raw anchor with a javascript target", '<a href="javascript:alert(1)">x</a>'],
  ["a javascript link", "[x](javascript:alert(1))"],
  ["a mixed-case javascript link", "[x](JaVaScRiPt:alert(1))"],
  ["a javascript link with an entity", "[x](&#106;avascript:alert(1))"],
  ["a javascript link with a percent-encoded newline", "[x](java%0ascript:alert(1))"],
  ["a data link", "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)"],
  ["a vbscript link", "[x](vbscript:msgbox(1))"],
  ["an image whose source is javascript", "![x](javascript:alert(1))"],
  ["a remote image (a tracking pixel)", "![x](http://evil.example/pixel.png)"],
  ["a link whose target tries to break out of the attribute", '[x](http://a.example/"onmouseover="alert(1))'],
  ["a script in a heading", "# <img src=x onerror=alert(1)>"],
  ["a script in a table cell", "| a | b |\n| - | - |\n| <img src=x onerror=alert(1)> | <script>alert(1)</script> |"],
  ["a script in a list item", "- <img src=x onerror=alert(1)>\n- [ ] <script>alert(1)</script>"],
  ["a script in a block quote", "> <script>alert(1)</script>"],
  ["a script in a fenced block", "```html\n<script>alert(1)</script>\n```"],
  ["a script in inline code", "`<script>alert(1)</script>`"],
  ["a script inside emphasis", "**<b onclick=alert(1)>x</b>** and *<i onmouseover=alert(1)>y</i>*"],
  ["a script in the link text", "[<img src=x onerror=alert(1)>](https://a.example)"],
  ["null bytes and control characters", "<scr\u0000ipt>alert(1)</scr\u0000ipt>\u202e"],
];

const DANGEROUS_SCHEME = /^\s*(?:javascript|data|vbscript):/i;

/** Elements the source must never produce. A page's own icons are svg, so a page check leaves svg out (its attributes are still scanned). */
const FORBIDDEN = "script, img, iframe, object, embed, style, meta, base, form, link, video, audio, input:not([type=checkbox])";

function expectInert(container: Element, forbidden: string = `${FORBIDDEN}, svg`): void {
  expect(container.querySelectorAll(forbidden)).toHaveLength(0);
  for (const element of container.querySelectorAll("*")) {
    for (const attribute of element.getAttributeNames()) {
      expect(attribute.startsWith("on"), `${element.tagName} has ${attribute}`).toBe(false);
    }
  }
  for (const anchor of container.querySelectorAll("a[href]")) {
    expect(anchor.getAttribute("href") ?? "", "href").not.toMatch(DANGEROUS_SCHEME);
    if (anchor.getAttribute("target") === "_blank") expect(anchor.getAttribute("rel")).toBe("noopener noreferrer");
  }
}

describe("Markdown renders hostile text as inert text", () => {
  it.each(VECTORS)("%s", (_name, source) => {
    const { container } = render(<Markdown source={source} />);

    expectInert(container);
  });

  it("shows the raw HTML as visible text", () => {
    render(<Markdown source={"<script>alert(1)</script> and <img src=x onerror=alert(1)>"} />);

    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(screen.getByText(/onerror=alert\(1\)>/)).toBeInTheDocument();
  });

  it("a hostile link keeps its words and loses its target", () => {
    const { container } = render(<Markdown source="[click me](javascript:alert(1))" />);

    expect(container).toHaveTextContent("click me");
    expect(container.querySelector("a")).toBeNull();
  });

  it("an external link opens in a new tab without an opener", () => {
    render(<Markdown source="[docs](https://github.com/AlexWaha/agentry)" />);

    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://github.com/AlexWaha/agentry");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("a relative link stays in the app and a task id becomes an app link", async () => {
    await renderWithApp(<Markdown source="[plan](/tasks/task-0104) and task-0104" />);

    expect(screen.getByRole("link", { name: "plan" })).toHaveAttribute("href", "/tasks/task-0104");
    expect(screen.getByRole("link", { name: "plan" })).not.toHaveAttribute("target");
    expect(screen.getAllByRole("link", { name: "task-0104" }).length).toBeGreaterThan(0);
  });

  it("a task-list item is a disabled checkbox with the right state", () => {
    render(<Markdown source={"- [x] done item\n- [ ] open item"} />);

    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toBeChecked();
    expect(boxes[0]).toBeDisabled();
    expect(boxes[1]).not.toBeChecked();
    expect(boxes[1]).toBeDisabled();
  });
});

describe("safeLink", () => {
  it.each([
    ["https://a.example/x", true],
    ["http://a.example", true],
    ["/tasks/task-0001", true],
    ["tasks/task-0001", true],
    ["#section", true],
    ["?page=2", true],
    ["javascript:alert(1)", false],
    ["JAVASCRIPT:alert(1)", false],
    ["data:text/html,x", false],
    ["vbscript:x", false],
    ["file:///etc/passwd", false],
    ["blob:https://a.example/1", false],
    ["ftp://a.example/x", false],
    ["mailto:a@b.example", false],
    ["ws://a.example", false],
  ])("%s is %s", (raw, allowed) => {
    expect(safeLink(raw) !== null).toBe(allowed);
  });

  it.each(["//evil.example/x", "///evil.example", "/\\evil.example", "http:evil.example"])(
    "%s is marked external: it leaves the app, so it opens in a new tab",
    (raw) => {
      expect(safeLink(raw)?.external).toBe(true);
    },
  );
});

describe("hostile text in the pages that render markdown", () => {
  const PAYLOAD = "<img src=x onerror=alert(1)> <script>alert(2)</script>\n\n[x](javascript:alert(3))";

  it("a task body", async () => {
    const detail = detailFixture.body.data;
    await renderApp("/tasks/task-0104", {
      routes: {
        "/api/v1/tasks/task-0104": {
          status: 200,
          body: { data: { ...detail, title: PAYLOAD, body: `## Description\n\n${PAYLOAD}\n\n## Acceptance Criteria\n\n- [ ] ${PAYLOAD}\n\n## Notes\n\n${PAYLOAD}` } },
        },
      },
    });
    await settle(200);

    expect(screen.getByRole("main")).toHaveTextContent("<script>alert(2)</script>");
    expectInert(screen.getByRole("main"), FORBIDDEN);
    expectInert(document.body, "script, img, iframe, object, embed, style, meta, base, form");
  });

  it("a handoff document and its problems", async () => {
    const doc = handoffFixture.body.data;
    await renderApp("/handoffs/task-0107", {
      routes: {
        "/api/v1/handoffs/task-0107": {
          status: 200,
          body: { data: { ...doc, valid: false, title: PAYLOAD, author: PAYLOAD, body: `# ${PAYLOAD}\n\n${PAYLOAD}`, problems: [PAYLOAD] } },
        },
      },
    });
    await settle(200);

    expect(screen.getByRole("main")).toHaveTextContent("<script>alert(2)</script>");
    expectInert(screen.getByRole("main"), FORBIDDEN);
    expectInert(document.body, "script, img, iframe, object, embed, style, meta, base, form");
  });
});

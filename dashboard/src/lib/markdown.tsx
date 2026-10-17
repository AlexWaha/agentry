import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/*
 * Safe markdown for task files and handoff documents (FR-87). Builds React elements only, so every piece of
 * text is escaped by React; there is no HTML injection path anywhere in this file. Raw HTML in the source
 * stays visible as text, and a link target is kept only when it parses as http, https or a relative path.
 * Supported: headings, paragraphs, ordered and unordered lists (one nesting level), task-list checkboxes,
 * tables, block quotes, fenced and inline code, bold, italic, links, and task ids as in-app links.
 */

const SAFE_BASE = "https://safe.invalid/";
const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\][]{1,500}\]\((?:[^()\s]|\([^()\s]{0,500}\)){1,2048}\))|(\btask-\d{4,}\b)/g;
// The `s` flag lets `.` and so `$` match across a stray line separator (U+2028); without it a failed `$` backtracks quadratically.
const LIST_ITEM = /^(\s*)([-*]|\d+\.)\s+(.*)$/s;
const CHECKBOX = /^\[( |x|X)\]\s+(.*)$/s;
const BLOCK_START = /^(```|#{1,6} |\||>|(-{3,}|\*{3,})$)/;

type SafeLink = { href: string; external: boolean };

/** A link target that is http(s) or relative, or null (javascript:, data:, vbscript: and the like). */
export function safeLink(raw: string): SafeLink | null {
  try {
    // "https:host" has no slashes, so a browser on an http page reads it as an absolute https URL: treat it as one.
    const url = new URL(raw.replace(/^(https?):\/*/i, "$1://"), SAFE_BASE);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    const external = url.origin !== new URL(SAFE_BASE).origin;
    return { href: external ? url.href : raw, external };
  } catch {
    return null;
  }
}

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const token = match[0];
    const key = `${keyPrefix}.${n++}`;
    if (match[1]) {
      out.push(
        <code key={key} className="rounded border border-border bg-muted px-1 font-mono text-xs wrap-anywhere">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[2]) {
      out.push(<strong key={key}>{inline(token.slice(2, -2), key)}</strong>);
    } else if (match[3]) {
      out.push(<em key={key}>{inline(token.slice(1, -1), key)}</em>);
    } else if (match[4]) {
      const parts = /^\[([^\]]+)\]\((.+)\)$/s.exec(token);
      const link = parts ? safeLink(parts[2]) : null;
      if (parts && link) {
        out.push(
          <a
            key={key}
            href={link.href}
            className="text-foreground underline underline-offset-2 wrap-anywhere"
            {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
          >
            {parts[1]}
          </a>,
        );
      } else {
        out.push(parts ? parts[1] : token);
      }
    } else {
      out.push(
        <Link key={key} to={ROUTES.task} params={{ taskId: token }} className="text-foreground underline underline-offset-2">
          {token}
        </Link>,
      );
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export interface MarkdownSection {
  /** The `## ` heading text; empty for the text before the first `## ` heading. */
  title: string;
  lines: string[];
}

/** Splits a body at its `## ` headings (fenced code is respected). Empty leading text is dropped. */
export function splitSections(body: string): MarkdownSection[] {
  const sections: MarkdownSection[] = [{ title: "", lines: [] }];
  let fenced = false;
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("```")) fenced = !fenced;
    const heading = !fenced ? /^## (.+)$/s.exec(line) : null;
    if (heading) sections.push({ title: heading[1].trim(), lines: [] });
    else sections[sections.length - 1].lines.push(line);
  }
  return sections.filter((section) => section.title || section.lines.some((line) => line.trim()));
}

interface ListItem {
  indent: number;
  text: string;
  children: ListItem[];
}

export interface RenderedMarkdown {
  nodes: ReactNode[];
  /** Task-list items that are checked, and all task-list items. */
  checked: number;
  total: number;
}

const HEADING_CLASS = "mt-4 text-[12.5px] font-semibold text-foreground wrap-anywhere";

/** Renders markdown lines to React nodes and counts the task-list checkboxes. */
export function renderMarkdown(lines: string[]): RenderedMarkdown {
  const nodes: ReactNode[] = [];
  let checked = 0;
  let total = 0;
  let i = 0;

  const itemNode = (item: ListItem, key: string): ReactNode => {
    const box = CHECKBOX.exec(item.text);
    const children = item.children.length ? (
      <ul className="mt-1.5 grid list-none gap-1.5 pl-0">{item.children.map((child, j) => itemNode(child, `${key}.${j}`))}</ul>
    ) : null;
    if (!box) {
      return (
        <li key={key} className="wrap-anywhere">
          {inline(item.text, key)}
          {children}
        </li>
      );
    }
    const done = box[1] !== " ";
    total += 1;
    if (done) checked += 1;
    return (
      <li key={key} className="grid grid-cols-[1rem_minmax(0,1fr)] items-start gap-2.5 wrap-anywhere">
        <input
          type="checkbox"
          checked={done}
          disabled
          readOnly
          aria-label={done ? t("common.done") : t("common.notDone")}
          className="mt-1 size-4 accent-status-done-edge"
        />
        <div>
          {inline(box[2], key)}
          {children}
        </div>
      </li>
    );
  };

  while (i < lines.length) {
    const line = lines[i];
    const key = String(nodes.length);
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i += 1;
      nodes.push(
        <pre key={key} className="overflow-auto rounded-md border border-border bg-muted px-3 py-2.5 font-mono text-xs">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = /^(#{1,6}) (.+)$/s.exec(line);
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 6)}` as "h2" | "h3" | "h4" | "h5" | "h6";
      nodes.push(
        <Tag key={key} className={HEADING_CLASS}>
          {inline(heading[2], key)}
        </Tag>,
      );
      i += 1;
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      nodes.push(<hr key={key} className="border-border" />);
      i += 1;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].replace(/^\||\|\s*$/g, "").split("|").map((cell) => cell.trim());
        if (!cells.every((cell) => /^:?-{2,}:?$/.test(cell))) rows.push(cells);
        i += 1;
      }
      if (rows.length === 0) continue;
      const [head, ...body] = rows;
      nodes.push(
        <div key={key} className="overflow-x-auto">
          <table className="border-collapse text-[13px]">
            <thead>
              <tr>
                {head.map((cell, j) => (
                  <th key={j} className="border border-border bg-muted px-2.5 py-1 text-left font-semibold">
                    {inline(cell, `${key}.h${j}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border border-border px-2.5 py-1 align-top">
                      {inline(cell, `${key}.${r}.${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      nodes.push(
        <blockquote key={key} className="border-l-2 border-border-strong pl-3 text-muted-foreground wrap-anywhere">
          {inline(quote.join(" "), key)}
        </blockquote>,
      );
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const top: ListItem[] = [];
      let topIndent = -1;
      let tasks = false;
      while (i < lines.length) {
        const match = LIST_ITEM.exec(lines[i]);
        if (match) {
          const item: ListItem = { indent: match[1].length, text: match[3], children: [] };
          if (topIndent < 0) topIndent = item.indent;
          if (item.indent > topIndent && top.length) top[top.length - 1].children.push(item);
          else top.push(item);
          if (/^\[( |x|X)\]/.test(match[3])) tasks = true;
          i += 1;
        } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && top.length) {
          const owner = top[top.length - 1];
          const target = owner.children.length ? owner.children[owner.children.length - 1] : owner;
          target.text += ` ${lines[i].trim()}`;
          i += 1;
        } else {
          break;
        }
      }
      const ordered = /^\s*\d+\./.test(line);
      const listClass = tasks ? "grid list-none gap-1.5 pl-0" : `grid gap-1.5 pl-5 ${ordered ? "list-decimal" : "list-disc"}`;
      const items = top.map((item, j) => itemNode(item, `${key}.${j}`));
      nodes.push(
        ordered ? (
          <ol key={key} className={listClass}>
            {items}
          </ol>
        ) : (
          <ul key={key} className={listClass}>
            {items}
          </ul>
        ),
      );
      continue;
    }
    const paragraph: string[] = [];
    while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i]) && !LIST_ITEM.test(lines[i])) {
      paragraph.push(lines[i++].trim());
    }
    if (paragraph.length === 0) {
      // A line that opens a block but did not parse as one (for example a lone "|"): show it as text.
      paragraph.push(lines[i++].trim());
    }
    nodes.push(
      <p key={key} className="text-secondary-foreground wrap-anywhere">
        {inline(paragraph.join(" "), key)}
      </p>,
    );
  }
  return { nodes, checked, total };
}

export interface MarkdownProps {
  source: string;
  className?: string;
}

/** Renders a markdown document. For sections with a checkbox counter use splitSections + renderMarkdown. */
export function Markdown({ source, className }: MarkdownProps) {
  const { nodes } = renderMarkdown(source.split(/\r?\n/));
  return <div className={cn("grid gap-2.5 text-sm leading-relaxed", className)}>{nodes}</div>;
}

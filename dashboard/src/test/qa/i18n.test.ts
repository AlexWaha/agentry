// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { NAV_ENTRIES } from "@/config/nav";
import { EVENT_KIND_KEYS, STAGE_LABEL_KEYS } from "@/config/stages";
import { GRAPH_VIEWS } from "@/features/graphs/search";
import { HARNESS_TABS } from "@/features/harness/search";
import { MEMORY_TABS, RETIRED_MODES } from "@/features/memory/search";
import { t } from "@/i18n";

/*
 * QA: every string the user can read comes from the catalog (FR-88). The catalog is checked from three sides:
 * the keys the code builds at run time exist, every placeholder has a value at its call sites, and no shipped
 * JSX prints a literal English word.
 */

const SRC = path.resolve(import.meta.dirname, "../..");
const EM_DASH = String.fromCharCode(0x2014);
const EN_DASH = String.fromCharCode(0x2013);

interface SourceFile {
  file: string;
  text: string;
}

function sources(filter: (file: string) => boolean): SourceFile[] {
  return readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .map((entry) => entry.replaceAll("\\", "/"))
    .filter((file) => /\.(ts|tsx)$/.test(file) && filter(file))
    .map((file) => ({ file, text: readFileSync(path.join(SRC, file), "utf8") }));
}

const shipped = sources((file) => !/\.test\.tsx?$/.test(file) && !file.startsWith("test/") && !file.startsWith("i18n/messages/"));

function parse({ file, text }: SourceFile): ts.SourceFile {
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}

function walk(node: ts.Node, visit: (node: ts.Node) => void): void {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

const groups = import.meta.glob<Record<string, string>>("../../i18n/messages/*.ts", { eager: true, import: "default" });

function loadCatalog(): Record<string, string> {
  return Object.assign({}, ...Object.values(groups)) as Record<string, string>;
}

describe("keys built at run time resolve", () => {
  const missing = (keys: string[]) => keys.filter((key) => t(key) === key);

  it("status chips, nav, stages, event kinds, theme and connection", () => {
    expect(missing(["backlog", "active", "done", "need"].map((status) => `chip.status.${status}`))).toEqual([]);
    expect(missing(NAV_ENTRIES.map((entry) => entry.labelKey))).toEqual([]);
    expect(missing(Object.values(STAGE_LABEL_KEYS))).toEqual([]);
    expect(missing(Object.values(EVENT_KIND_KEYS))).toEqual([]);
    expect(missing(["light", "dark", "system"].map((choice) => `theme.${choice}`))).toEqual([]);
    expect(missing(["live", "reconnecting", "offline"].map((state) => `conn.${state}`))).toEqual([]);
  });

  it("memory, graphs and harness families", () => {
    expect(missing(MEMORY_TABS.map((tab) => `memory.tab.${tab}`))).toEqual([]);
    expect(missing(["lesson", "pattern", "module"].map((kind) => `memory.noun.${kind}`))).toEqual([]);
    expect(missing(["exclude", ...RETIRED_MODES].map((mode) => `memory.filter.retired.${mode}`))).toEqual([]);
    expect(missing(GRAPH_VIEWS.map((view) => `graphs.view.${view}`))).toEqual([]);
    expect(missing(["lesson", "pattern", "task", "agent", "area", "module", "file"].map((kind) => `graphs.kind.${kind}`))).toEqual([]);
    expect(missing(HARNESS_TABS.map((tab) => `harness.tab.${tab}`))).toEqual([]);
    expect(missing(["directory", "cache"].map((install) => `harness.plugin.install.${install}`))).toEqual([]);
    const deliveries = ["core", "main-thread", "targeted", "unused"];
    expect(missing(deliveries.flatMap((delivery) => [`harness.rules.${delivery}`, `harness.rules.${delivery}.hint`]))).toEqual([]);
  });

  it("the Needs-you chip text of the checkpoint kinds the pipeline can park at", () => {
    expect(missing(["plan", "commit", "push", "trunk_push", "blocked"].map((kind) => `needs.kind.${kind}`))).toEqual([]);
  });
});

describe("placeholders", () => {
  /** Every call `t("literal.key", { name, other: x })` with an object literal: the names it supplies. */
  function callSites(): { file: string; key: string; supplied: string[] | null }[] {
    const out: { file: string; key: string; supplied: string[] | null }[] = [];
    for (const source of shipped) {
      walk(parse(source), (node) => {
        if (!ts.isCallExpression(node) || !ts.isIdentifier(node.expression) || node.expression.text !== "t") return;
        const [first, second] = node.arguments;
        if (!first || !ts.isStringLiteralLike(first)) return;
        if (!second) {
          out.push({ file: source.file, key: first.text, supplied: [] });
        } else if (ts.isObjectLiteralExpression(second)) {
          const names = second.properties.flatMap((property) =>
            property.name && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name)) ? [property.name.text] : [],
          );
          out.push({ file: source.file, key: first.text, supplied: names });
        } else {
          out.push({ file: source.file, key: first.text, supplied: null });
        }
      });
    }
    return out;
  }

  it("a call supplies a value for every {placeholder} of its message, and only those", () => {
    const catalog = loadCatalog();
    const problems: string[] = [];
    for (const { file, key, supplied } of callSites()) {
      const message = catalog[key];
      if (message === undefined || supplied === null) continue;
      const wanted = [...message.matchAll(/\{(\w+)\}/g)].map((match) => match[1]);
      for (const name of wanted) if (!supplied.includes(name)) problems.push(`${file}: t("${key}") does not supply {${name}}`);
      for (const name of supplied) if (!wanted.includes(name)) problems.push(`${file}: t("${key}") supplies ${name}, which the message does not use`);
    }

    expect(problems).toEqual([]);
  });
});

describe("no literal English in the shipped JSX", () => {
  const WORD = /[A-Za-z]{2,}/;
  const TEXT_ATTRIBUTES = new Set(["aria-label", "aria-description", "title", "placeholder", "alt", "label"]);

  it("prints no word from JSX text, and no text attribute set to a string literal", () => {
    const hits: string[] = [];
    for (const source of shipped.filter(({ file }) => file.endsWith(".tsx"))) {
      walk(parse(source), (node) => {
        if (ts.isJsxText(node) && WORD.test(node.text)) hits.push(`${source.file}: text "${node.text.trim().slice(0, 40)}"`);
        if (
          ts.isJsxAttribute(node) &&
          TEXT_ATTRIBUTES.has(node.name.getText()) &&
          node.initializer &&
          ts.isStringLiteral(node.initializer) &&
          WORD.test(node.initializer.text)
        ) {
          hits.push(`${source.file}: ${node.name.getText()}="${node.initializer.text}"`);
        }
      });
    }

    expect(hits).toEqual([]);
  });
});

describe("punctuation (NFR-12)", () => {
  it("no em dash and no en dash in any file of the dashboard source, index.html or the public scripts", () => {
    const root = path.resolve(SRC, "..");
    const files = [
      ...readdirSync(SRC, { recursive: true, encoding: "utf8" }).map((entry) => path.join(SRC, entry)),
      ...readdirSync(path.join(root, "public"), { encoding: "utf8" }).map((entry) => path.join(root, "public", entry)),
      path.join(root, "index.html"),
      path.join(root, "package.json"),
    ].filter((file) => /\.(ts|tsx|css|json|html|js|svg)$/.test(file));
    const offenders = files.filter((file) => {
      const text = readFileSync(file, "utf8");
      return text.includes(EM_DASH) || text.includes(EN_DASH);
    });

    expect(offenders).toEqual([]);
  });

  it("every message is plain ASCII English text", () => {
    const catalog = loadCatalog();
    const odd = Object.entries(catalog)
      .filter(([, text]) => /[^\x20-\x7e]/.test(text))
      .map(([key, text]) => `${key}: ${text}`);

    expect(odd).toEqual([]);
  });
});

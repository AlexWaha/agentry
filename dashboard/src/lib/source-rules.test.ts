// @vitest-environment node
import { ESLint } from "eslint";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import docsLinks from "@/config/docs-links.json";
import { ROUTES } from "@/config/routes";
import { STAGE_LABEL_KEYS, EVENT_KIND_KEYS } from "@/config/stages";
import { STORAGE_KEYS } from "@/config/storage-keys";
import { NAV_ENTRIES } from "@/config/nav";
import { t } from "@/i18n";
import shellMessages from "@/i18n/messages/shell";

/*
 * Rules of the codebase that no type or linter states, enforced by scanning the shipped source
 * (everything under src/ except tests and src/test). FR-81, FR-85, FR-87, FR-88, FR-124.
 */

const ROOT = path.resolve(import.meta.dirname, "../..");
const SRC = path.join(ROOT, "src");

type Sources = Record<string, string>;

function shippedSources(): Sources {
  const out: Sources = {};
  for (const entry of readdirSync(SRC, { recursive: true, encoding: "utf8" })) {
    const file = entry.replaceAll("\\", "/");
    if (!/\.(ts|tsx|css)$/.test(file) || /\.test\.(ts|tsx)$/.test(file) || file.startsWith("test/")) continue;
    out[`/src/${file}`] = readFileSync(path.join(SRC, file), "utf8");
  }
  return out;
}

const sources = shippedSources();
const codeSources = Object.fromEntries(Object.entries(sources).filter(([file]) => /\.tsx?$/.test(file)));

/* ---------- --attention (FR-85) ---------- */

const ATTENTION_USE = /--attention\b|\b(?:bg|text|border|ring|fill|stroke|outline|from|via|to|shadow|decoration|accent|caret|divide)-attention\b/;
const ATTENTION_ALLOWED = [
  /^\/src\/styles\//,
  /^\/src\/features\/needs\//,
  /^\/src\/components\/shared\/(?:StatusChip|StatCard|Stepper|EventTimeline)\.tsx$/,
  /^\/src\/app\/Sidebar\.tsx$/,
];

/** Files that reference the attention token or an attention utility outside the Needs-you components. */
function attentionMisuse(files: Sources): string[] {
  return Object.entries(files)
    .filter(([file, text]) => ATTENTION_USE.test(text) && !ATTENTION_ALLOWED.some((allowed) => allowed.test(file)))
    .map(([file]) => file);
}

describe("--attention rule", () => {
  it("passes on the shipped source", () => {
    expect(attentionMisuse(sources)).toEqual([]);
  });

  it.each([
    ["a board card using the utility", "/src/features/board/BoardCard.tsx", '<div className="bg-attention-bg" />'],
    ["a shared chip using the text utility", "/src/components/shared/Chip.tsx", '<span className="hover:text-attention" />'],
    ["a page reading the variable", "/src/features/overview/Overview.tsx", 'style={{ color: "var(--attention)" }}'],
  ])("fails on %s", (_name, file, text) => {
    expect(attentionMisuse({ ...sources, [file]: text })).toEqual([file]);
  });

  it("accepts the Needs-you feature", () => {
    expect(attentionMisuse({ "/src/features/needs/NeedsRow.tsx": '<span className="text-attention" />' })).toEqual([]);
  });
});

/* ---------- unsafe HTML, polling, locale, raw colours ---------- */

describe("source hygiene", () => {
  it("never renders HTML from a string (FR-87)", () => {
    const hits = Object.entries(codeSources)
      .filter(([, text]) => /dangerouslySetInnerHTML|\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write\(/.test(text))
      .map(([file]) => file);

    expect(hits).toEqual([]);
  });

  it("has no refetchInterval anywhere (FR-81)", () => {
    const hits = Object.entries(codeSources)
      .filter(([, text]) => /refetchInterval/.test(text))
      .map(([file]) => file);

    expect(hits).toEqual([]);
  });

  it("inlines no locale string outside i18n/ (FR-88)", () => {
    const hits = Object.entries(codeSources)
      .filter(([file]) => !file.startsWith("/src/i18n/"))
      .filter(([, text]) => /(["'`])(?:en|en-[A-Z]{2})\1/.test(text))
      .map(([file]) => file);

    expect(hits).toEqual([]);
  });

  it("holds raw hex colours only in styles/", () => {
    const hits = Object.entries(sources)
      .filter(([file]) => !file.startsWith("/src/styles/"))
      .filter(([, text]) => /#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b(?![\w-])/.test(text))
      .map(([file]) => file);

    expect(hits).toEqual([]);
  });

  it("uses no inline style attribute", () => {
    const hits = Object.entries(codeSources)
      .filter(([, text]) => /\bstyle=\{\{/.test(text))
      .map(([file]) => file);

    expect(hits).toEqual([]);
  });
});

/* ---------- i18n ---------- */

describe("message catalog", () => {
  it("resolves every literal t() key used in the shipped source", () => {
    const missing: string[] = [];
    for (const [file, text] of Object.entries(codeSources)) {
      for (const match of text.matchAll(/\bt\(\s*"([A-Za-z][A-Za-z0-9.]*)"/g)) {
        if (t(match[1]) === match[1]) missing.push(`${file}: ${match[1]}`);
      }
    }

    expect(missing).toEqual([]);
  });

  it("resolves every key named by a config map", () => {
    const keys = [...Object.values(STAGE_LABEL_KEYS), ...Object.values(EVENT_KIND_KEYS), ...NAV_ENTRIES.map((entry) => entry.labelKey)];
    for (const status of ["backlog", "active", "done", "need"]) keys.push(`chip.status.${status}`);
    for (const theme of ["light", "dark", "system"]) keys.push(`theme.${theme}`);
    for (const state of ["live", "reconnecting", "offline"]) keys.push(`conn.${state}`);

    expect(keys.filter((key) => t(key) === key)).toEqual([]);
  });

  it("keeps the shell file inside its key prefixes", () => {
    const owned = /^(shell|common|pager|chip|error|theme|conn|docs)\./;

    expect(Object.keys(shellMessages).filter((key) => !owned.test(key))).toEqual([]);
  });
});

/* ---------- config ---------- */

describe("config tables", () => {
  it("has one docs-links entry per page id and none extra", () => {
    expect(Object.keys(docsLinks).sort()).toEqual(Object.keys(ROUTES).sort());
  });

  it("points every docs link at a file under docs/ with an https-free relative path", () => {
    for (const entry of Object.values(docsLinks)) {
      expect(entry.path).toMatch(/^docs\/[\w-]+\.md$/);
      expect(entry.anchor).toMatch(/^[a-z0-9-]*$/);
    }
  });
});

/* ---------- index.html, theme script, favicon (FR-84, FR-86) ---------- */

const indexHtml = readFileSync(path.join(ROOT, "index.html"), "utf8");

describe("index.html", () => {
  it("loads the theme script as an external blocking script in the head and has no inline script", () => {
    const head = indexHtml.slice(indexHtml.indexOf("<head>"), indexHtml.indexOf("</head>"));

    expect(head).toMatch(/<script src="\/theme-init\.js"><\/script>/);
    expect(indexHtml.match(/<script\b[^>]*>/g)?.every((tag) => /\bsrc=/.test(tag))).toBe(true);
    expect(indexHtml).not.toMatch(/<script\b[^>]*>\s*[^<\s]/);
  });

  it("references no external URL", () => {
    expect(indexHtml).not.toMatch(/https?:\/\//);
    expect(indexHtml).not.toMatch(/(?:src|href)="\/\//);
  });

  it("links the svg and ico favicons", () => {
    expect(indexHtml).toContain('href="/favicon.svg"');
    expect(indexHtml).toContain('href="/favicon.ico"');
  });
});

describe("public assets", () => {
  it("ships an ico with 16, 32 and 48 px images", () => {
    const ico = readFileSync(path.join(ROOT, "public/favicon.ico"));
    const count = ico.readUInt16LE(4);
    const sizes = Array.from({ length: count }, (_, index) => ico[6 + index * 16]);

    expect(ico.readUInt16LE(2)).toBe(1);
    expect(sizes.sort((a, b) => a - b)).toEqual([16, 32, 48]);
  });

  it("ships an svg favicon with the logo artwork", () => {
    const svg = readFileSync(path.join(ROOT, "public/favicon.svg"), "utf8");

    expect(svg).toContain("<svg");
    expect(svg).toContain("#E8590C");
  });

  it("applies the stored theme before first paint from the same storage key as the app", () => {
    const script = readFileSync(path.join(ROOT, "public/theme-init.js"), "utf8");

    expect(script).toContain(`getItem("${STORAGE_KEYS.theme}")`);
  });

  it.each([
    ["dark", false, true],
    ["light", true, false],
    ["system", true, true],
    ["system", false, false],
    [null, true, true],
    [null, false, false],
  ] as const)("theme script with saved %s and OS dark=%s gives dark=%s", (saved, osDark, expected) => {
    const script = readFileSync(path.join(ROOT, "public/theme-init.js"), "utf8");
    const classes = new Set<string>();
    const fakeWindow = { matchMedia: () => ({ matches: osDark }) };
    const fakeDocument = {
      documentElement: {
        classList: {
          toggle: (name: string, force: boolean) => {
            if (force) classes.add(name);
            else classes.delete(name);
          },
        },
      },
    };
    const fakeStorage = { getItem: () => saved };

    new Function("window", "document", "localStorage", script)(fakeWindow, fakeDocument, fakeStorage);

    expect(classes.has("dark")).toBe(expected);
  });

  it("survives blocked storage by following the system", () => {
    const script = readFileSync(path.join(ROOT, "public/theme-init.js"), "utf8");
    const classes = new Set<string>();
    const fakeDocument = {
      documentElement: { classList: { toggle: (name: string, force: boolean) => (force ? classes.add(name) : classes.delete(name)) } },
    };
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
    };

    new Function("window", "document", "localStorage", script)({ matchMedia: () => ({ matches: true }) }, fakeDocument, blocked);

    expect(classes.has("dark")).toBe(true);
  });
});

/* ---------- Pager scan (FR-124) ---------- */

describe("list pages", () => {
  const LIST_PAGES = [
    "src/features/tasks/TasksPage.tsx",
    "src/features/activity/ActivityPage.tsx",
    "src/features/memory/MemoryPage.tsx",
    "src/features/handoffs/HandoffsPage.tsx",
  ];

  it.each(LIST_PAGES)("%s, when present, uses the shared Pager", (page) => {
    const file = path.join(ROOT, page);
    if (!existsSync(file)) return;

    expect(readFileSync(file, "utf8")).toMatch(/from "@\/components\/shared\/Pager"/);
  });

  it("defines the Pager only once", () => {
    const definitions = Object.entries(codeSources)
      .filter(([, text]) => /export function Pager\b|function Pager\(/.test(text))
      .map(([file]) => file);

    expect(definitions).toEqual(["/src/components/shared/Pager.tsx"]);
  });
});

/* ---------- ESLint boundary rule (FR-75) ---------- */

describe("ESLint boundaries", () => {
  const eslint = new ESLint({ cwd: ROOT });

  async function rulesFor(file: string, code: string): Promise<string[]> {
    const [result] = await eslint.lintText(code, { filePath: path.join(ROOT, file) });
    return result.messages.map((message) => message.ruleId ?? message.message);
  }

  const CROSS = "boundaries/no-cross-feature-import";

  it("fails a feature that imports another feature by alias", async () => {
    expect(await rulesFor("src/features/board/BoardPage.tsx", 'import { TasksPage } from "@/features/tasks/TasksPage";\nexport const a = TasksPage;\n')).toContain(CROSS);
  });

  it("fails a feature that imports another feature by relative path", async () => {
    expect(await rulesFor("src/features/board/BoardPage.tsx", 'import { TasksPage } from "../tasks/TasksPage";\nexport const a = TasksPage;\n')).toContain(CROSS);
  });

  it("fails a re-export and a dynamic import from another feature", async () => {
    expect(await rulesFor("src/features/board/index.ts", 'export { x } from "@/features/tasks/x";\n')).toContain(CROSS);
    expect(await rulesFor("src/features/board/lazy.ts", 'export const load = () => import("@/features/tasks/TasksPage");\n')).toContain(CROSS);
  });

  it("fails shared code that imports a feature", async () => {
    expect(await rulesFor("src/components/shared/Chip.tsx", 'import { x } from "@/features/board/x";\nexport const a = x;\n')).toContain(CROSS);
  });

  it("allows a feature to import shared code and itself", async () => {
    const code = 'import { Pager } from "@/components/shared/Pager";\nimport { card } from "./BoardCard";\nimport { size } from "../board/size";\nexport const a = [Pager, card, size];\n';

    expect(await rulesFor("src/features/board/BoardPage.tsx", code)).not.toContain(CROSS);
  });

  it("allows app and test code to reach every feature", async () => {
    const code = 'import { createRoutes } from "@/features/board/routes";\nexport const a = createRoutes;\n';

    expect(await rulesFor("src/app/router.tsx", code)).not.toContain(CROSS);
    expect(await rulesFor("src/test/qa/board.test.tsx", code)).not.toContain(CROSS);
  });

  it("rejects dangerouslySetInnerHTML and refetchInterval in application code", async () => {
    const html = await rulesFor("src/features/board/Raw.tsx", 'export const A = () => <div dangerouslySetInnerHTML={{ __html: "x" }} />;\n');
    const poll = await rulesFor("src/features/board/poll.ts", "export const options = { refetchInterval: 1000 };\n");

    expect(html).toContain("no-restricted-syntax");
    expect(poll).toContain("no-restricted-syntax");
  });
});

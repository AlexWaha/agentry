import { act, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { STORAGE_KEYS } from "@/config/storage-keys";
import { applyTheme, readThemeChoice } from "@/lib/theme";
import { installMatchMedia, setSystemDark } from "@/test/utils/match-media";

import { declarationsOf, ratio, roles } from "./contrast";
import { qaUser, renderApp, settle } from "./app";

/*
 * QA: Light / Dark / System through the real shell (FR-84, User decision "theme live"), the before-first-paint
 * script, a blocked or corrupted store, and the contrast of the two palettes read from the token files.
 */

const ROOT = path.resolve(import.meta.dirname, "../../..");
const dark = () => document.documentElement.classList.contains("dark");
const radio = (name: string) => screen.getByRole("radio", { name });

describe("theme switch in the shell", () => {
  it("Dark, Light and System change the class at once, store the choice and reload nothing", async () => {
    const user = qaUser();
    const app = await renderApp("/tasks");
    await settle(150);
    const requests = app.api.requests.length;

    await user.click(radio("Dark"));
    expect(dark()).toBe(true);
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe("dark");

    await user.click(radio("Light"));
    expect(dark()).toBe(false);
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe("light");

    await user.click(radio("System"));
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe("system");
    expect(app.api.requests.length).toBe(requests);
  });

  it("System follows the OS live in both directions, a fixed choice does not", async () => {
    const user = qaUser();
    await renderApp("/");
    await settle(100);
    expect(radio("System")).toBeChecked();

    act(() => setSystemDark(true));
    expect(dark()).toBe(true);
    act(() => setSystemDark(false));
    expect(dark()).toBe(false);

    await user.click(radio("Light"));
    act(() => setSystemDark(true));
    expect(dark()).toBe(false);

    await user.click(radio("Dark"));
    act(() => setSystemDark(false));
    expect(dark()).toBe(true);
  });

  it("a saved choice is restored by a new page load", async () => {
    localStorage.setItem(STORAGE_KEYS.theme, "dark");
    await renderApp("/");
    await settle(100);

    expect(radio("Dark")).toBeChecked();
    expect(dark()).toBe(true);
  });

  it("the choice survives navigation between pages", async () => {
    const user = qaUser();
    const app = await renderApp("/");
    await settle(100);
    await user.click(radio("Dark"));

    await user.click(screen.getByRole("link", { name: "Board" }));
    await settle(100);

    expect(app.router.state.location.pathname).toBe("/board");
    expect(dark()).toBe(true);
    expect(radio("Dark")).toBeChecked();
  });

  it("switching the theme does not remount the page", async () => {
    const user = qaUser();
    await renderApp("/tasks");
    await settle(150);
    const list = screen.getByRole("list", { name: "Tasks" });

    await user.click(radio("Dark"));

    expect(screen.getByRole("list", { name: "Tasks" })).toBe(list);
  });
});

describe("the script that sets the class before first paint", () => {
  const script = readFileSync(path.join(ROOT, "public/theme-init.js"), "utf8");
  const run = () => new Function(script)();

  const CHOICES: (string | null)[] = [null, "", "light", "dark", "system", "bogus", "DARK", "Dark ", "{}"];

  it.each(CHOICES.flatMap((choice) => [true, false].map((osDark) => [choice, osDark] as const)))(
    "stored %j with the OS dark=%s gives the same class as the app",
    (choice, osDark) => {
      installMatchMedia(osDark);
      if (choice !== null) localStorage.setItem(STORAGE_KEYS.theme, choice);

      document.documentElement.className = "";
      run();
      const early = dark();

      document.documentElement.className = "";
      applyTheme(readThemeChoice());

      expect(early).toBe(dark());
    },
  );

  it("survives a blocked store and follows the OS", () => {
    installMatchMedia(true);
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    document.documentElement.className = "";
    expect(run).not.toThrow();
    expect(dark()).toBe(true);
    getItem.mockRestore();
  });

  it("is an external file with no inline script left in index.html", () => {
    const html = readFileSync(path.join(ROOT, "index.html"), "utf8");

    expect(html).toMatch(/<script src="\/theme-init\.js"><\/script>/);
    expect(html.indexOf("theme-init.js")).toBeLessThan(html.indexOf("</head>"));
    expect(html.match(/<script(?![^>]*\bsrc=)[^>]*>/g)).toBeNull();
  });
});

describe("a blocked or corrupted store", () => {
  it("falls back to System and still lets the user choose", async () => {
    const user = qaUser();
    localStorage.setItem(STORAGE_KEYS.theme, "purple");
    await renderApp("/");
    await settle(100);

    expect(radio("System")).toBeChecked();
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    await user.click(radio("Dark"));
    expect(dark()).toBe(true);
    setItem.mockRestore();
  });
});

/* ---------- contrast of the palettes, read from the token files ---------- */

describe.each(["light", "dark"] as const)("%s palette contrast", (mode) => {
  const TEXT: [string, string, string?][] = [
    ["--foreground", "--background"],
    ["--foreground", "--card"],
    ["--secondary-foreground", "--card"],
    ["--muted-foreground", "--card"],
    ["--muted-foreground", "--muted"],
    ["--muted-foreground", "--background"],
    ["--muted-foreground", "--sidebar"],
    ["--status-backlog-fg", "--card", "--status-backlog-bg"],
    ["--status-active-fg", "--card", "--status-active-bg"],
    ["--status-done-fg", "--card", "--status-done-bg"],
    ["--attention", "--card", "--attention-bg"],
    ["--primary-foreground", "--primary"],
    ["--accent-foreground", "--accent"],
  ];

  it.each(TEXT)("text %s on %s (tint %s) reaches 4.5:1", (fg, bg, tint) => {
    expect(ratio(mode, fg, bg, tint)).toBeGreaterThanOrEqual(4.5);
  });

  it("the text on the filled orange badge reaches 4.5:1", () => {
    expect(ratio(mode, "--on-attention", "--attention")).toBeGreaterThanOrEqual(4.5);
  });

  it.each(["--status-backlog-edge", "--status-active-edge", "--status-done-edge", "--ring"])("graphic %s against the card reaches 3:1", (edge) => {
    expect(ratio(mode, edge, "--card")).toBeGreaterThanOrEqual(3);
  });

  it("every role of the light palette exists in the dark palette (only on-status may be shared)", () => {
    const missing = [...roles.light.keys()].filter((name) => !declarationsOf("dark").has(name) && name !== "--on-status" && name !== "color-scheme");

    expect(missing).toEqual([]);
  });
});

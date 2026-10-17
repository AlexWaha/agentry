import { readFileSync } from "node:fs";
import path from "node:path";

/*
 * WCAG contrast of the palettes, computed from the committed token files (styles/tokens.css and theme.css), so a
 * palette change that breaks a text or graphic contrast fails a test without a browser.
 */

type Rgb = [number, number, number];

const ROOT = path.resolve(import.meta.dirname, "../../..");
const tokens = readFileSync(path.join(ROOT, "src/styles/tokens.css"), "utf8");
const theme = readFileSync(path.join(ROOT, "src/styles/theme.css"), "utf8");

function declarations(block: string): Map<string, string> {
  return new Map([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2].trim()]));
}

const raw = declarations(tokens);
const rootBlock = /:root\s*\{([\s\S]*?)\n\}/.exec(theme)?.[1] ?? "";
const darkBlock = /\.dark\s*\{([\s\S]*?)\n\}/.exec(theme)?.[1] ?? "";
export const roles = { light: declarations(rootBlock), dark: new Map([...declarations(rootBlock), ...declarations(darkBlock)]) };

function resolve(value: string, scope: Map<string, string>): string {
  return value.replace(/var\((--[\w-]+)\)/g, (_whole, name: string) => resolve(scope.get(name) ?? raw.get(name) ?? "", scope));
}

function parseColour(value: string, scope: Map<string, string>): { rgb: Rgb; alpha: number } {
  const text = resolve(value, scope).trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(text);
  if (hex) return { rgb: [0, 2, 4].map((at) => parseInt(hex[1].slice(at, at + 2), 16)) as Rgb, alpha: 1 };
  const fn = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\s*\)$/.exec(text);
  if (fn) return { rgb: [Number(fn[1]), Number(fn[2]), Number(fn[3])], alpha: fn[4] === undefined ? 1 : Number(fn[4]) };
  throw new Error(`cannot read colour "${value}" -> "${text}"`);
}

function over(top: { rgb: Rgb; alpha: number }, below: Rgb): Rgb {
  return top.rgb.map((channel, index) => Math.round(channel * top.alpha + below[index] * (1 - top.alpha))) as Rgb;
}

function luminance([r, g, b]: Rgb): number {
  const linear = (channel: number) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export function ratio(mode: "light" | "dark", fg: string, bg: string, tint?: string): number {
  const scope = roles[mode];
  const surface = parseColour(scope.get(bg) ?? "", scope).rgb;
  const ground = tint ? over(parseColour(scope.get(tint) ?? "", scope), surface) : surface;
  return contrast(over(parseColour(scope.get(fg) ?? "", scope), ground), ground);
}


/** The role declarations of one palette block as written (no inheritance), for completeness checks. */
export function declarationsOf(mode: "light" | "dark"): Map<string, string> {
  return declarations(mode === "light" ? rootBlock : darkBlock);
}

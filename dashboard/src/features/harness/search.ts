import { enumParam } from "@/lib/search-params";

/** The tabs of the Harness page, in display order. The selected one lives in the `tab` search param (FR-127). */
export const HARNESS_TABS = ["agents", "rules", "skills", "hooks", "plugin"] as const;

export type HarnessTab = (typeof HARNESS_TABS)[number];

export const DEFAULT_TAB: HarnessTab = "agents";

export interface HarnessSearch {
  tab?: HarnessTab;
}

/** The tab of a raw search value: an unknown value falls back to Agents. */
export function tabOf(raw: unknown): HarnessTab {
  return enumParam(raw, HARNESS_TABS) ?? DEFAULT_TAB;
}

/** Route search validation: the default tab is omitted from the URL and an unknown value is dropped. */
export function validateHarnessSearch(raw: Record<string, unknown>): HarnessSearch {
  const tab = tabOf(raw.tab);
  return { tab: tab === DEFAULT_TAB ? undefined : tab };
}

const OPEN_GROUP = "(";
const CLOSE_GROUP = ")";
const ALTERNATIVE = "|";

/** A hook matcher such as `Edit|Write|(a|b)` as its top-level alternatives, so each one becomes a wrapping chip. */
export function splitMatcher(matcher: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of matcher) {
    if (char === OPEN_GROUP) depth += 1;
    if (char === CLOSE_GROUP) depth = Math.max(0, depth - 1);
    if (char === ALTERNATIVE && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => part.trim()).filter((part) => part !== "");
}

/*
 * Lightweight message catalog (frontend-standards, i18n-module). Every user-facing string is looked up here
 * with t(key, params). The shipped locale is English only. Messages live in i18n/messages/<group>.ts, one
 * file per key prefix owner, and are merged at build time by a glob: adding a group is adding a file.
 */

export const DEFAULT_LOCALE = "en";

export type MessageMap = Record<string, string>;

/** Merge message groups into one catalog; the same key in two groups is a defect, so it throws. */
export function mergeMessages(groups: Record<string, MessageMap>): MessageMap {
  const merged: MessageMap = {};
  const origin: Record<string, string> = {};
  for (const [file, messages] of Object.entries(groups)) {
    for (const [key, text] of Object.entries(messages)) {
      if (Object.hasOwn(merged, key)) {
        throw new Error(`Duplicate i18n key "${key}" in ${file} (already defined in ${origin[key]})`);
      }
      merged[key] = text;
      origin[key] = file;
    }
  }
  return merged;
}

const groups = import.meta.glob<MessageMap>("./messages/*.ts", { eager: true, import: "default" });

const catalogs: Record<string, MessageMap> = { [DEFAULT_LOCALE]: mergeMessages(groups) };

export type MessageParams = Record<string, string | number>;

/** Fills `{name}` placeholders; a placeholder without a value stays as written. */
export function interpolate(template: string, params?: MessageParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (Object.hasOwn(params, name) ? String(params[name]) : whole));
}

/** The text for a key. A missing key renders the key name, so a gap is visible in the UI and in review. */
export function t(key: string, params?: MessageParams): string {
  const template = catalogs[DEFAULT_LOCALE]?.[key];
  return template === undefined ? key : interpolate(template, params);
}

export function useTranslation(): { t: typeof t; locale: string } {
  return { t, locale: DEFAULT_LOCALE };
}

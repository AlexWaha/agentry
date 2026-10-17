import { useCallback, useEffect, useState } from "react";

import { STORAGE_KEYS } from "@/config/storage-keys";

/*
 * Light, Dark and System (FR-84). The choice is stored per origin. "system" follows the OS colour scheme live
 * through a matchMedia listener. The class on <html> is first set before paint by public/theme-init.js; this
 * module keeps it in step afterwards.
 */

export type ThemeChoice = "light" | "dark" | "system";

export const THEME_CHOICES: readonly ThemeChoice[] = ["light", "dark", "system"];

const DARK_QUERY = "(prefers-color-scheme: dark)";
const DARK_CLASS = "dark";

function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === "light" || value === "dark" || value === "system";
}

export function readThemeChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEYS.theme);
    return isThemeChoice(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

function osPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function applyTheme(choice: ThemeChoice): void {
  const dark = choice === "dark" || (choice === "system" && osPrefersDark());
  document.documentElement.classList.toggle(DARK_CLASS, dark);
}

function storeThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(STORAGE_KEYS.theme, choice);
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
}

export function useTheme(): { choice: ThemeChoice; setChoice: (choice: ThemeChoice) => void } {
  const [choice, setChoiceState] = useState<ThemeChoice>(readThemeChoice);

  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system" || typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(DARK_QUERY);
    const onChange = (): void => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [choice]);

  const setChoice = useCallback((next: ThemeChoice) => {
    storeThemeChoice(next);
    setChoiceState(next);
  }, []);

  return { choice, setChoice };
}

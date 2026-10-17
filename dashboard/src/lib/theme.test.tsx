import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ThemeSwitch } from "@/app/ThemeSwitch";
import { STORAGE_KEYS } from "@/config/storage-keys";
import { applyTheme, readThemeChoice } from "@/lib/theme";
import { installMatchMedia, setSystemDark } from "@/test/utils/match-media";

const isDark = (): boolean => document.documentElement.classList.contains("dark");

describe("theme switch", () => {
  it("offers Light, Dark and System and starts on System", () => {
    render(<ThemeSwitch />);

    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "System" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Light" })).not.toBeChecked();
  });

  it("stores Dark, applies the dark class and restores it on reload", async () => {
    const first = render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("radio", { name: "Dark" }));

    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe("dark");
    expect(isDark()).toBe(true);

    first.unmount();
    document.documentElement.classList.remove("dark");
    render(<ThemeSwitch />);

    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(isDark()).toBe(true);
  });

  it("applies Light over a dark OS preference", async () => {
    installMatchMedia(true);
    render(<ThemeSwitch />);
    expect(isDark()).toBe(true);

    await userEvent.click(screen.getByRole("radio", { name: "Light" }));

    expect(isDark()).toBe(false);
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe("light");
  });

  it("follows a matchMedia change live while System is selected", () => {
    render(<ThemeSwitch />);
    expect(isDark()).toBe(false);

    setSystemDark(true);
    expect(isDark()).toBe(true);

    setSystemDark(false);
    expect(isDark()).toBe(false);
  });

  it("ignores OS changes once a fixed theme is chosen", async () => {
    render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("radio", { name: "Light" }));

    setSystemDark(true);

    expect(isDark()).toBe(false);
  });

  it("stops listening to the OS when the switch unmounts", () => {
    const view = render(<ThemeSwitch />);
    view.unmount();

    setSystemDark(true);

    expect(isDark()).toBe(false);
  });
});

describe("theme helpers", () => {
  it("reads an unknown or missing stored value as System", () => {
    expect(readThemeChoice()).toBe("system");
    localStorage.setItem(STORAGE_KEYS.theme, "purple");
    expect(readThemeChoice()).toBe("system");
    localStorage.setItem(STORAGE_KEYS.theme, "dark");
    expect(readThemeChoice()).toBe("dark");
  });

  it("applies dark for System only when the OS prefers dark", () => {
    installMatchMedia(true);
    applyTheme("system");
    expect(isDark()).toBe(true);

    installMatchMedia(false);
    applyTheme("system");
    expect(isDark()).toBe(false);
  });
});

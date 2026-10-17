/*
 * A controllable window.matchMedia for jsdom, which has none. Only the dark-scheme query is meaningful:
 * every other query reports `matches: false`. setSystemDark() flips the OS preference and fires `change`.
 */

type Listener = (event: { matches: boolean; media: string }) => void;

interface MockMediaQueryList {
  matches: boolean;
  media: string;
  addEventListener: (type: "change", listener: Listener) => void;
  removeEventListener: (type: "change", listener: Listener) => void;
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

let systemDark = false;
const listeners = new Set<Listener>();

export function installMatchMedia(initialDark = false): void {
  systemDark = initialDark;
  listeners.clear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string): MockMediaQueryList => ({
      get matches() {
        return query === DARK_QUERY && systemDark;
      },
      media: query,
      addEventListener: (_type, listener) => {
        if (query === DARK_QUERY) listeners.add(listener);
      },
      removeEventListener: (_type, listener) => {
        listeners.delete(listener);
      },
    }),
  });
}

/** Changes the OS colour scheme and notifies every dark-scheme listener, as a real change event would. */
export function setSystemDark(dark: boolean): void {
  systemDark = dark;
  for (const listener of [...listeners]) listener({ matches: dark, media: DARK_QUERY });
}

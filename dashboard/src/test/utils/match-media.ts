/*
 * A controllable window.matchMedia for jsdom, which has none. Two queries are meaningful, the dark scheme and
 * reduced motion; every other query reports `matches: false`. setSystemDark() and setReducedMotion() flip the
 * OS preference and fire `change`.
 */

type Listener = (event: { matches: boolean; media: string }) => void;

interface MockMediaQueryList {
  matches: boolean;
  media: string;
  addEventListener: (type: "change", listener: Listener) => void;
  removeEventListener: (type: "change", listener: Listener) => void;
}

const DARK_QUERY = "(prefers-color-scheme: dark)";
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

let systemDark = false;
let reducedMotion = false;
const listeners = new Set<Listener>();
const motionListeners = new Set<Listener>();

export function installMatchMedia(initialDark = false): void {
  systemDark = initialDark;
  reducedMotion = false;
  listeners.clear();
  motionListeners.clear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string): MockMediaQueryList => ({
      get matches() {
        return (query === DARK_QUERY && systemDark) || (query === REDUCED_MOTION_QUERY && reducedMotion);
      },
      media: query,
      addEventListener: (_type, listener) => {
        if (query === DARK_QUERY) listeners.add(listener);
        if (query === REDUCED_MOTION_QUERY) motionListeners.add(listener);
      },
      removeEventListener: (_type, listener) => {
        listeners.delete(listener);
        motionListeners.delete(listener);
      },
    }),
  });
}

/** Changes the OS colour scheme and notifies every dark-scheme listener, as a real change event would. */
export function setSystemDark(dark: boolean): void {
  systemDark = dark;
  for (const listener of [...listeners]) listener({ matches: dark, media: DARK_QUERY });
}

/** Changes the OS reduced-motion preference and notifies every listener. */
export function setReducedMotion(reduce: boolean): void {
  reducedMotion = reduce;
  for (const listener of [...motionListeners]) listener({ matches: reduce, media: REDUCED_MOTION_QUERY });
}

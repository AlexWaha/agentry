import { useSyncExternalStore } from "react";

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(REDUCED_MOTION_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

const read = (): boolean => typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION_QUERY).matches;

/** True while the OS asks for reduced motion; follows a change of the setting without a reload. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}

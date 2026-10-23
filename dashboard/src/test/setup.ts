import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { resetToken } from "@/lib/token";
import { installCanvasStub } from "@/test/utils/canvas";
import { installMatchMedia, setReducedMotion } from "@/test/utils/match-media";

// A test file can opt into the node environment (`// @vitest-environment node`), which has no DOM to prepare.
const hasDom = typeof window !== "undefined";

beforeEach(() => {
  if (!hasDom) return;
  installMatchMedia(false);
  // Reduced motion is ON for every test, on purpose: it makes the Graphs page default to its 2D mode. The QA suites
  // render the whole app, /graphs included, in jsdom, which has no WebGL; without this each of them would load the real
  // three.js chunk and fall back with a console error. Tests of the 3D mode turn it off or pass ?mode=3d, and mock
  // the 3D module (graph3d.ts) or 3d-force-graph; reduced-motion.test.tsx sets the preference itself.
  setReducedMotion(true);
  // jsdom has no canvas: every test gets a recording 2D context instead of a "not implemented" error.
  installCanvasStub();
  window.scrollTo = () => undefined;
});

afterEach(() => {
  if (!hasDom) return;
  cleanup();
  localStorage.clear();
  resetToken();
  document.documentElement.className = "";
  window.history.replaceState(null, "", "/");
});

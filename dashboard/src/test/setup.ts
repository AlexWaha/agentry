import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { resetToken } from "@/lib/token";
import { installCanvasStub } from "@/test/utils/canvas";
import { installMatchMedia } from "@/test/utils/match-media";

// A test file can opt into the node environment (`// @vitest-environment node`), which has no DOM to prepare.
const hasDom = typeof window !== "undefined";

beforeEach(() => {
  if (!hasDom) return;
  installMatchMedia(false);
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

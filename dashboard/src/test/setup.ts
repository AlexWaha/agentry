import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { resetToken } from "@/lib/token";
import { installMatchMedia } from "@/test/utils/match-media";

// A test file can opt into the node environment (`// @vitest-environment node`), which has no DOM to prepare.
const hasDom = typeof window !== "undefined";

beforeEach(() => {
  if (!hasDom) return;
  installMatchMedia(false);
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

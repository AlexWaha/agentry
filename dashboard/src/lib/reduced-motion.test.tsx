import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setReducedMotion } from "@/test/utils/match-media";

import { usePrefersReducedMotion } from "./reduced-motion";

describe("usePrefersReducedMotion", () => {
  it("is false when the OS does not ask for reduced motion", () => {
    setReducedMotion(false);

    const { result } = renderHook(() => usePrefersReducedMotion());

    expect(result.current).toBe(false);
  });

  it("starts true when the OS already asks for it", () => {
    setReducedMotion(true);

    const { result } = renderHook(() => usePrefersReducedMotion());

    expect(result.current).toBe(true);
  });

  it("follows a change of the setting without a reload", () => {
    const { result } = renderHook(() => usePrefersReducedMotion());

    act(() => setReducedMotion(true));
    expect(result.current).toBe(true);
    act(() => setReducedMotion(false));

    expect(result.current).toBe(false);
  });

  it("stops listening when the component unmounts", () => {
    const { unmount } = renderHook(() => usePrefersReducedMotion());

    unmount();

    expect(() => setReducedMotion(true)).not.toThrow();
  });
});

import { vi } from "vitest";

/*
 * jsdom has no canvas: getContext returns null and logs "not implemented". This stands in a 2D context that
 * records every method call, so a test can read what was drawn (the text of every label, how many frames were
 * painted) without pixels. Properties such as fillStyle are stored and ignored.
 */

export interface CanvasCall {
  name: string;
  args: unknown[];
}

export interface CanvasStub {
  calls: CanvasCall[];
  /** The text of every fillText call, in order. */
  labels: () => string[];
  /** Forgets the calls so far. */
  clear: () => void;
  restore: () => void;
}

export function installCanvasStub(): CanvasStub {
  const calls: CanvasCall[] = [];
  const stored: Record<string | symbol, unknown> = {};
  const context = new Proxy(stored, {
    get: (target, property) =>
      property in target
        ? target[property]
        : (...args: unknown[]) => {
            calls.push({ name: String(property), args });
          },
    set: (target, property, value: unknown) => {
      target[property] = value;
      return true;
    },
  });
  const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  return {
    calls,
    labels: () => calls.filter((call) => call.name === "fillText").map((call) => String(call.args[0])),
    clear: () => {
      calls.length = 0;
    },
    restore: () => spy.mockRestore(),
  };
}

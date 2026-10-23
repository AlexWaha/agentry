import { vi } from "vitest";

/*
 * jsdom has no canvas: getContext returns null and logs "not implemented". This stands in a 2D context that
 * records every method call and every property assignment (as "set <property>"), so a test can read what was
 * drawn (the text and place of every label, the colours and widths used) without pixels. A text measures 6 pixels
 * per character so label placement can be checked.
 */

export const CHARACTER_PX = 6;

export interface CanvasCall {
  name: string;
  args: unknown[];
}

export interface LabelDrawn {
  text: string;
  x: number;
  y: number;
}

export interface CanvasStub {
  calls: CanvasCall[];
  /** The text of every fillText call, in order. */
  labels: () => string[];
  /** The text and position of every fillText call, in order. */
  labelsDrawn: () => LabelDrawn[];
  /** The values assigned to one context property (`globalAlpha`, `strokeStyle`, `lineWidth`), in order. */
  assigned: (property: string) => unknown[];
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
        : property === "measureText"
          ? (text: string) => ({ width: text.length * CHARACTER_PX })
          : (...args: unknown[]) => {
              calls.push({ name: String(property), args });
            },
    set: (target, property, value: unknown) => {
      target[property] = value;
      calls.push({ name: `set ${String(property)}`, args: [value] });
      return true;
    },
  });
  const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const texts = (): CanvasCall[] => calls.filter((call) => call.name === "fillText");
  return {
    calls,
    labels: () => texts().map((call) => String(call.args[0])),
    labelsDrawn: () => texts().map((call) => ({ text: String(call.args[0]), x: Number(call.args[1]), y: Number(call.args[2]) })),
    assigned: (property) => calls.filter((call) => call.name === `set ${property}`).map((call) => call.args[0]),
    clear: () => {
      calls.length = 0;
    },
    restore: () => spy.mockRestore(),
  };
}

import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { usePrefersReducedMotion } from "@/lib/reduced-motion";
import { cn } from "@/lib/utils";
import type { GraphEdge, GraphNode } from "@/types/api";

import { ZOOM_STEP } from "./config";
import type { EngineOptions, EngineView, GraphEngine } from "./engine";
import { createGraphEngine } from "./forceGraph";
import { GraphDetails } from "./GraphDetails";
import { kindLabel } from "./kinds";
import { buildScene } from "./layout";
import type { GraphMode } from "./search";
import type { Direction } from "./view";

export interface GraphCanvasProps {
  /** Accessible name of the drawing. */
  title: string;
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  /** `2d` is the canvas view, `3d` the lazily loaded WebGL scene. The parent re-mounts this component on a change. */
  mode: GraphMode;
  /** Called when the 3D scene cannot start, with why (no WebGL, or its chunk failed to load); the parent falls back to 2D. */
  onUnavailable?: (reason: Unavailable3d) => void;
}

/** Why the 3D scene did not start: the browser has no WebGL, or the chunk with three.js could not be loaded. */
export type Unavailable3d = "webgl" | "load";

const ARROWS: Record<string, Direction> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
const VIEW_CLASS =
  "block h-[min(70vh,34rem)] min-h-72 w-full touch-none rounded-md bg-muted outline-none focus-visible:ring-2 focus-visible:ring-ring data-[hover=true]:cursor-pointer";

/**
 * The drawing of a graph (FR-106, FR-108): a live force simulation that zooms and lets a node be dragged, as a 2D
 * canvas or as a 3D scene that also orbits. The view is ONE tab stop, and the keys are the same in both: the
 * arrow keys move to the nearest node in that direction, PageUp, PageDown, Home and End walk all nodes most
 * linked first (the 3D camera glides to the node reached), Enter or Space selects the current node and
 * highlights its neighbours, Escape clears, plus and minus zoom, 0 resets, and in 3D Shift with an arrow key
 * orbits the camera. The current node, and a selection, are announced in a live region; "Show as table" is the
 * full alternative.
 */
export function GraphCanvas({ title, nodes, edges, mode, onUnavailable }: GraphCanvasProps) {
  const scene = useMemo(() => buildScene(nodes, edges), [nodes, edges]);
  const reducedMotion = usePrefersReducedMotion();
  const elementRef = useRef<HTMLElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const viewRef = useRef<EngineView>({ selected: null, focused: null });
  const unavailableRef = useRef(onUnavailable);
  const [notice, setNotice] = useState<string | null>(null);
  const hintId = useId();
  const [ready, setReady] = useState(mode === "2d");
  const [picked, setPicked] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasFocus, setHasFocus] = useState(false);
  // A press with the mouse focuses the view too, but only keyboard focus draws the ring and the label.
  const pointerFocus = useRef(false);

  const selected = picked ? scene.byId.get(picked) : undefined;
  const current = (cursor ? scene.byId.get(cursor) : undefined) ?? scene.ordered[0];
  const focused = hasFocus ? current : undefined;

  useEffect(() => {
    unavailableRef.current = onUnavailable;
  }, [onUnavailable]);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return undefined;
    let cancelled = false;
    let engine: GraphEngine | null = null;
    const options: EngineOptions = {
      reducedMotion,
      onSelect: (id) => setPicked((now) => (id === null || id === now ? null : id)),
    };
    const start = (create: () => GraphEngine): void => {
      engine = create();
      engineRef.current = engine;
      engine.setView(viewRef.current);
    };
    if (mode === "2d") {
      start(() => createGraphEngine(element as HTMLCanvasElement, scene, options));
    } else {
      import("./graph3d").then(
        ({ createGraph3D }) => {
          if (cancelled) return;
          try {
            start(() => createGraph3D(element, scene, options));
            setReady(true);
          } catch {
            unavailableRef.current?.("webgl");
          }
        },
        () => {
          if (!cancelled) unavailableRef.current?.("load");
        },
      );
    }
    return () => {
      cancelled = true;
      engine?.destroy();
      engineRef.current = null;
    };
  }, [scene, reducedMotion, mode]);

  useEffect(() => {
    viewRef.current = { selected: selected?.id ?? null, focused: focused?.id ?? null };
    engineRef.current?.setView(viewRef.current);
  }, [scene, reducedMotion, mode, selected?.id, focused?.id]);

  /** Drops the selection and says so, so the live region never keeps announcing a selection that is gone. */
  const clearSelection = useCallback((): void => {
    setNotice(selected ? t("graphs.node.cleared", { label: selected.label }) : null);
    setPicked(null);
  }, [selected]);

  const resetView = useCallback((): void => {
    clearSelection();
    engineRef.current?.reset();
  }, [clearSelection]);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    const engine = engineRef.current;
    if (!engine || event.ctrlKey || event.metaKey || event.altKey) return;
    const at = current ? scene.ordered.indexOf(current) : -1;
    const walkTo = (index: number): void => {
      const node = scene.ordered[Math.min(scene.ordered.length - 1, Math.max(0, index))];
      if (!node) return;
      setNotice(null);
      setCursor(node.id);
      engine.reveal(node.id);
    };
    const direction = ARROWS[event.key];
    setHasFocus(true);
    if (direction && event.shiftKey && engine.orbit) {
      engine.orbit(direction);
    } else if (direction) {
      if (current) {
        const next = engine.move(current.id, direction);
        if (next) {
          setNotice(null);
          setCursor(next);
        }
      }
    } else if (event.key === "Home") walkTo(0);
    else if (event.key === "End") walkTo(scene.ordered.length - 1);
    else if (event.key === "PageDown") walkTo(at + 1);
    else if (event.key === "PageUp") walkTo(at - 1);
    else if (event.key === "Enter" || event.key === " ") {
      if (!current) return;
      const selecting = picked !== current.id;
      setPicked(selecting ? current.id : null);
      setNotice(t(selecting ? "graphs.node.selected" : "graphs.node.cleared", { label: current.label }));
    } else if (event.key === "Escape") clearSelection();
    else if (event.key === "+" || event.key === "=") engine.zoomBy(ZOOM_STEP);
    else if (event.key === "-" || event.key === "_") engine.zoomBy(1 / ZOOM_STEP);
    else if (event.key === "0") resetView();
    else return;
    event.preventDefault();
  };

  const viewProps = {
    role: "application",
    tabIndex: 0,
    "aria-label": t("graphs.canvas.label", { title, nodes: nodes.length, edges: edges.length }),
    "aria-describedby": hintId,
    onKeyDown,
    onPointerDown: () => {
      pointerFocus.current = true;
    },
    onFocus: () => {
      setHasFocus(!pointerFocus.current);
      pointerFocus.current = false;
    },
    onBlur: () => {
      setHasFocus(false);
      setNotice(null);
      pointerFocus.current = false;
    },
  };

  return (
    <div className="min-w-0">
      <div role="group" aria-label={t("graphs.controls.label")} className="mb-2 flex flex-wrap items-center justify-end gap-2">
        <Button size="sm" onClick={() => engineRef.current?.fit()}>
          {t("graphs.fit")}
        </Button>
        <Button size="sm" onClick={resetView}>
          {t("graphs.reset")}
        </Button>
      </div>
      <div className="relative">
        {mode === "2d" ? (
          <canvas ref={elementRef as RefObject<HTMLCanvasElement>} {...viewProps} className={VIEW_CLASS} />
        ) : (
          <div ref={elementRef as RefObject<HTMLDivElement>} {...viewProps} data-mode="3d" className={cn(VIEW_CLASS, "relative overflow-hidden")} />
        )}
        {ready ? null : (
          <p role="status" className="pointer-events-none absolute inset-0 grid place-items-center text-xs text-muted-foreground">
            {t("graphs.loading3d")}
          </p>
        )}
      </div>
      <div aria-live="polite" className="sr-only">
        {notice ?? (focused ? t("graphs.node.label", { kind: kindLabel(focused.kind), label: focused.label, links: focused.degree }) : null)}
      </div>
      {selected ? (
        <GraphDetails
          node={selected}
          onClear={() => {
            clearSelection();
            // The button is about to disappear: keep the keyboard where the user was, on the graph.
            elementRef.current?.focus();
          }}
        />
      ) : null}
      <p id={hintId} className="mt-2 text-xs text-muted-foreground">
        {t(mode === "2d" ? "graphs.hint" : "graphs.hint3d")}
      </p>
    </div>
  );
}

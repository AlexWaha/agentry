import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { usePrefersReducedMotion } from "@/lib/reduced-motion";
import type { GraphEdge, GraphNode } from "@/types/api";

import { ZOOM_STEP } from "./config";
import { createGraphEngine, type GraphEngine } from "./forceGraph";
import { GraphDetails } from "./GraphDetails";
import { kindLabel } from "./kinds";
import { buildScene } from "./layout";
import type { Direction } from "./view";

export interface GraphCanvasProps {
  /** Accessible name of the drawing. */
  title: string;
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
}

const ARROWS: Record<string, Direction> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };

/**
 * The drawing of a graph (FR-106, FR-108): a live force simulation on a canvas that zooms, pans and lets a node
 * be dragged. The canvas is ONE tab stop. Arrow keys move to the nearest node in that direction, PageUp,
 * PageDown, Home and End walk all nodes most linked first, Enter or Space highlights the neighbours of the
 * current node, Escape clears, plus and minus zoom, 0 resets. The current node is announced in a live region
 * and its label is drawn; "Show as table" is the full alternative.
 */
export function GraphCanvas({ title, nodes, edges }: GraphCanvasProps) {
  const scene = useMemo(() => buildScene(nodes, edges), [nodes, edges]);
  const reducedMotion = usePrefersReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const hintId = useId();
  const [picked, setPicked] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasFocus, setHasFocus] = useState(false);
  // A press with the mouse focuses the canvas too, but only keyboard focus draws the ring and the label.
  const pointerFocus = useRef(false);

  const selected = picked ? scene.byId.get(picked) : undefined;
  const current = (cursor ? scene.byId.get(cursor) : undefined) ?? scene.ordered[0];
  const focused = hasFocus ? current : undefined;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const engine = createGraphEngine(canvas, scene, {
      reducedMotion,
      onSelect: (id) => setPicked((now) => (id === null || id === now ? null : id)),
    });
    engineRef.current = engine;
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, [scene, reducedMotion]);

  useEffect(() => {
    engineRef.current?.setView({ selected: selected?.id ?? null, focused: focused?.id ?? null });
  }, [scene, reducedMotion, selected?.id, focused?.id]);

  const resetView = useCallback((): void => {
    setPicked(null);
    engineRef.current?.reset();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>): void => {
    const engine = engineRef.current;
    if (!engine || event.ctrlKey || event.metaKey || event.altKey) return;
    const at = current ? scene.ordered.indexOf(current) : -1;
    const walkTo = (index: number): void => {
      const node = scene.ordered[Math.min(scene.ordered.length - 1, Math.max(0, index))];
      if (!node) return;
      setCursor(node.id);
      engine.reveal(node.id);
    };
    const direction = ARROWS[event.key];
    setHasFocus(true);
    if (direction) {
      if (current) {
        const next = engine.move(current.id, direction);
        if (next) setCursor(next);
      }
    } else if (event.key === "Home") walkTo(0);
    else if (event.key === "End") walkTo(scene.ordered.length - 1);
    else if (event.key === "PageDown") walkTo(at + 1);
    else if (event.key === "PageUp") walkTo(at - 1);
    else if (event.key === "Enter" || event.key === " ") setPicked((now) => (current && now !== current.id ? current.id : null));
    else if (event.key === "Escape") setPicked(null);
    else if (event.key === "+" || event.key === "=") engine.zoomBy(ZOOM_STEP);
    else if (event.key === "-" || event.key === "_") engine.zoomBy(1 / ZOOM_STEP);
    else if (event.key === "0") resetView();
    else return;
    event.preventDefault();
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
      <canvas
        ref={canvasRef}
        role="application"
        tabIndex={0}
        aria-label={t("graphs.canvas.label", { title, nodes: nodes.length, edges: edges.length })}
        aria-describedby={hintId}
        onKeyDown={onKeyDown}
        onPointerDown={() => {
          pointerFocus.current = true;
        }}
        onFocus={() => {
          setHasFocus(!pointerFocus.current);
          pointerFocus.current = false;
        }}
        onBlur={() => {
          setHasFocus(false);
          pointerFocus.current = false;
        }}
        className="block h-[min(70vh,34rem)] min-h-72 w-full touch-none rounded-md bg-muted outline-none focus-visible:ring-2 focus-visible:ring-ring data-[hover=true]:cursor-pointer"
      />
      <div aria-live="polite" className="sr-only">
        {focused ? t("graphs.node.label", { kind: kindLabel(focused.kind), label: focused.label, links: focused.degree }) : null}
      </div>
      {selected ? <GraphDetails node={selected} onClear={() => setPicked(null)} /> : null}
      <p id={hintId} className="mt-2 text-xs text-muted-foreground">
        {t("graphs.hint")}
      </p>
    </div>
  );
}

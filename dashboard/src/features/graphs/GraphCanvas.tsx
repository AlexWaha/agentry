import { useMemo, useRef, useState, type KeyboardEvent } from "react";

import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { GraphEdge, GraphNode } from "@/types/api";

import { kindClass, kindLabel } from "./kinds";
import { GRAPH_HEIGHT, GRAPH_WIDTH, layoutGraph } from "./layout";

export interface GraphCanvasProps {
  /** Accessible name of the drawing. */
  title: string;
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
}

type NodeState = "selected" | "neighbor" | "dimmed" | "idle";

/** Ids of the nodes one edge away from `id`. */
function neighboursOf(id: string, edges: readonly GraphEdge[]): Set<string> {
  const out = new Set<string>();
  for (const edge of edges) {
    if (edge.source === id) out.add(edge.target);
    if (edge.target === id) out.add(edge.source);
  }
  return out;
}

/**
 * The SVG drawing of a graph (FR-106, FR-108). The nodes are buttons behind ONE tab stop (roving tabindex):
 * arrow keys, Home and End move between them, the label shows on focus and hover, Enter or Space (or a click)
 * highlights the neighbours and dims the rest, pressing it again clears. The table view is the full keyboard
 * alternative.
 */
export function GraphCanvas({ title, nodes, edges }: GraphCanvasProps) {
  const placed = useMemo(() => layoutGraph(nodes, edges), [nodes, edges]);
  const [selected, setSelected] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [stop, setStop] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const tabStop = nodes.some((node) => node.id === stop) ? stop : (nodes[0]?.id ?? null);
  const near = useMemo(() => (selected ? neighboursOf(selected, edges) : new Set<string>()), [selected, edges]);
  const degree = useMemo(() => {
    const counts = new Map<string, number>();
    for (const edge of edges) {
      counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1);
      counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1);
    }
    return counts;
  }, [edges]);

  const stateOf = (id: string): NodeState => {
    if (!selected) return "idle";
    if (id === selected) return "selected";
    return near.has(id) ? "neighbor" : "dimmed";
  };
  const toggle = (id: string): void => setSelected((current) => (current === id ? null : id));
  const moveFocus = (id: string, key: string): void => {
    const buttons = [...(svgRef.current?.querySelectorAll<SVGGElement>("[data-kind]") ?? [])];
    const at = buttons.findIndex((button) => button.dataset.id === id);
    const target = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: buttons.length - 1 }[key];
    if (target !== undefined) buttons[Math.min(buttons.length - 1, Math.max(0, target))]?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<SVGGElement>, id: string): void => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(id);
    } else if (event.key === "Escape") {
      setSelected(null);
    } else if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      moveFocus(id, event.key);
    }
  };

  return (
    <svg
      ref={svgRef}
      role="group"
      aria-label={t("graphs.canvas.label", { title, nodes: nodes.length, edges: edges.length })}
      viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`}
      className="block h-auto max-h-[70vh] w-full rounded-md bg-muted"
    >
      <g aria-hidden="true">
        {edges.map((edge, index) => {
          const from = placed.get(edge.source);
          const to = placed.get(edge.target);
          if (!from || !to) return null;
          const lit = !selected || edge.source === selected || edge.target === selected;
          return (
            <line
              key={`${edge.source}>${edge.target}#${index}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              className={cn("stroke-border-strong", lit ? "stroke-[1.5]" : "opacity-20")}
            />
          );
        })}
      </g>
      {nodes.map((node) => {
        const spot = placed.get(node.id);
        if (!spot) return null;
        const state = stateOf(node.id);
        const showLabel = node.id === active || state === "selected" || state === "neighbor";
        return (
          <g
            key={node.id}
            role="button"
            tabIndex={node.id === tabStop ? 0 : -1}
            aria-pressed={state === "selected"}
            aria-label={t("graphs.node.label", { kind: kindLabel(node.kind), label: node.label, links: degree.get(node.id) ?? 0 })}
            data-kind={node.kind}
            data-id={node.id}
            data-state={state}
            className={cn("group cursor-pointer outline-none", state === "dimmed" && "opacity-25")}
            onClick={() => toggle(node.id)}
            onKeyDown={(event) => onKeyDown(event, node.id)}
            onFocus={() => {
              setActive(node.id);
              setStop(node.id);
            }}
            onBlur={() => setActive((current) => (current === node.id ? null : current))}
            onMouseEnter={() => setActive(node.id)}
            onMouseLeave={() => setActive((current) => (current === node.id ? null : current))}
          >
            <circle
              cx={spot.x}
              cy={spot.y}
              r={spot.r}
              className={cn(
                kindClass(node.kind),
                "group-focus-visible:stroke-ring group-focus-visible:stroke-[3]",
                state === "selected" && "stroke-ring stroke-[3]",
              )}
            />
            {showLabel ? (
              <text
                x={spot.x}
                y={spot.y - spot.r - 5}
                textAnchor="middle"
                className="fill-foreground stroke-background stroke-[3] text-[11px] [paint-order:stroke]"
              >
                {node.label}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

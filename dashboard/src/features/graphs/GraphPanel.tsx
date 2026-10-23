import { useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { usePrefersReducedMotion } from "@/lib/reduced-motion";
import { useListSearch } from "@/lib/search-params";
import type { GraphEdge, GraphNode } from "@/types/api";

import { GraphCanvas, type Unavailable3d } from "./GraphCanvas";
import { GraphTable } from "./GraphTable";
import { kindClass, kindLabel } from "./kinds";
import { capGraph } from "./layout";
import { GRAPH_MODES, type GraphMode, type GraphsSearch } from "./search";

export interface GraphPanelProps {
  title: string;
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  /** Shown above the drawing, for example a truncation notice. */
  notice?: ReactNode;
}

/**
 * A graph with its legend, the 2D/3D switch and the "Show as table" switch (FR-106, FR-108). The mode is a search
 * param of the URL; absent, it is 3D, or 2D when the OS asks for reduced motion. A 3D scene that cannot start
 * (no WebGL) falls back to 2D with a notice.
 */
export function GraphPanel({ title, nodes, edges, notice }: GraphPanelProps) {
  const drawn = useMemo(() => capGraph(nodes, edges), [nodes, edges]);
  const { search, setFilters } = useListSearch<GraphsSearch>();
  const reducedMotion = usePrefersReducedMotion();
  const [asTable, setAsTable] = useState(false);
  const [no3d, setNo3d] = useState<Unavailable3d | null>(null);
  const requested: GraphMode = search.mode ?? (reducedMotion ? "2d" : "3d");
  const mode: GraphMode = requested === "3d" && no3d !== null ? "2d" : requested;
  const kinds = [...new Set(nodes.map((node) => node.kind))];
  return (
    <Card className="min-w-0 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <ul aria-label={t("graphs.legend")} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5">
          {kinds.map((kind) => (
            <li key={kind} className="flex items-center gap-1.5 text-[12.5px]">
              <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3">
                <circle cx="6" cy="6" r="4.5" className={kindClass(kind)} />
              </svg>
              {kindLabel(kind)}
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label={t("graphs.mode.label")} className="flex items-center gap-1">
            {GRAPH_MODES.map((value) => (
              <Button
                key={value}
                size="sm"
                variant={mode === value ? "default" : "outline"}
                aria-pressed={mode === value}
                onClick={() => {
                  setNo3d(null);
                  setFilters({ mode: value }, { replace: true });
                }}
              >
                {t(`graphs.mode.${value}`)}
              </Button>
            ))}
          </div>
          <Button size="sm" aria-pressed={asTable} onClick={() => setAsTable((current) => !current)}>
            {asTable ? t("graphs.showGraph") : t("graphs.showTable")}
          </Button>
        </div>
      </div>
      {notice}
      {asTable ? (
        <GraphTable nodes={nodes} edges={edges} />
      ) : (
        <>
          {drawn.nodes.length < nodes.length ? (
            <p role="status" className="mb-3 text-[12.5px] text-muted-foreground">
              {t("graphs.capped", { shown: drawn.nodes.length, total: nodes.length })}
            </p>
          ) : null}
          {requested === "3d" && no3d !== null ? (
            <p role="status" className="mb-3 rounded-md border border-dashed border-border-strong px-3 py-2 text-[12.5px] text-muted-foreground">
              {t(no3d === "load" ? "graphs.mode.loadFailed" : "graphs.mode.unavailable")}
            </p>
          ) : null}
          <GraphCanvas key={mode} title={title} nodes={drawn.nodes} edges={drawn.edges} mode={mode} onUnavailable={setNo3d} />
        </>
      )}
    </Card>
  );
}

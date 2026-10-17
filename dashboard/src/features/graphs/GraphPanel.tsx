import { useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { GraphEdge, GraphNode } from "@/types/api";

import { GraphCanvas } from "./GraphCanvas";
import { GraphTable } from "./GraphTable";
import { kindClass, kindLabel } from "./kinds";
import { capGraph } from "./layout";

export interface GraphPanelProps {
  title: string;
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  /** Shown above the drawing, for example a truncation notice. */
  notice?: ReactNode;
}

/** A graph with its legend, the keyboard hint and the "Show as table" switch (FR-106, FR-108). */
export function GraphPanel({ title, nodes, edges, notice }: GraphPanelProps) {
  const drawn = useMemo(() => capGraph(nodes, edges), [nodes, edges]);
  const [asTable, setAsTable] = useState(false);
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
        <Button size="sm" aria-pressed={asTable} onClick={() => setAsTable((current) => !current)}>
          {asTable ? t("graphs.showGraph") : t("graphs.showTable")}
        </Button>
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
          <GraphCanvas title={title} nodes={drawn.nodes} edges={drawn.edges} />
        </>
      )}
    </Card>
  );
}

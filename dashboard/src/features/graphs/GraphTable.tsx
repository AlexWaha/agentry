import { useState } from "react";

import { Pager } from "@/components/shared/Pager";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { lastPage } from "@/lib/search-params";
import type { GraphEdge, GraphNode } from "@/types/api";

import { TABLE_PAGE_SIZE } from "./config";
import { kindLabel } from "./kinds";

const TABLE_CLASS = "w-full min-w-96 border-collapse text-left";
const HEAD_CELL = "px-3 py-2 font-medium";
const CELL = "px-3 py-1.5 align-top wrap-anywhere";

/** The "Show as table" alternative of a graph (FR-108): every node and every edge as text. */
export function GraphTable({ nodes, edges }: { nodes: readonly GraphNode[]; edges: readonly GraphEdge[] }) {
  const labels = new Map(nodes.map((node) => [node.id, node.label]));
  const [requestedNodePage, setNodePage] = useState(1);
  const [requestedEdgePage, setEdgePage] = useState(1);
  const nodePage = Math.min(requestedNodePage, lastPage(nodes.length, TABLE_PAGE_SIZE));
  const edgePage = Math.min(requestedEdgePage, lastPage(edges.length, TABLE_PAGE_SIZE));
  const pageOf = <T,>(rows: readonly T[], page: number): readonly T[] => rows.slice((page - 1) * TABLE_PAGE_SIZE, page * TABLE_PAGE_SIZE);
  return (
    <div className="grid min-w-0 gap-5">
      <section>
        <SectionHead title={t("graphs.table.nodes")} right={nodes.length} />
        <Card className="max-h-96 overflow-auto">
          <table className={TABLE_CLASS}>
            <caption className="sr-only">{t("graphs.table.nodes")}</caption>
            <thead className="sticky top-0 bg-card text-xs text-muted-foreground">
              <tr>
                <th scope="col" className={HEAD_CELL}>{t("graphs.table.kind")}</th>
                <th scope="col" className={HEAD_CELL}>{t("graphs.table.label")}</th>
                <th scope="col" className={`${HEAD_CELL} text-right`}>{t("graphs.table.links")}</th>
              </tr>
            </thead>
            <tbody>
              {pageOf(nodes, nodePage).map((node) => (
                <tr key={node.id} className="border-t border-border">
                  <td className={CELL}>{kindLabel(node.kind)}</td>
                  <td className={`${CELL} font-mono text-[12px]`}>{node.label}</td>
                  <td className={`${CELL} text-right tabular-nums`}>{node.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        {nodes.length > TABLE_PAGE_SIZE ? (
          <Pager page={nodePage} size={TABLE_PAGE_SIZE} total={nodes.length} noun={t("graphs.table.nodesNoun")} onPage={setNodePage} />
        ) : null}
      </section>
      <section>
        <SectionHead title={t("graphs.table.edges")} right={edges.length} />
        <Card className="max-h-96 overflow-auto">
          <table className={TABLE_CLASS}>
            <caption className="sr-only">{t("graphs.table.edges")}</caption>
            <thead className="sticky top-0 bg-card text-xs text-muted-foreground">
              <tr>
                <th scope="col" className={HEAD_CELL}>{t("graphs.table.source")}</th>
                <th scope="col" className={HEAD_CELL}>{t("graphs.table.target")}</th>
                <th scope="col" className={HEAD_CELL}>{t("graphs.table.edgeKind")}</th>
              </tr>
            </thead>
            <tbody>
              {pageOf(edges, edgePage).map((edge, index) => (
                <tr key={`${edge.source}>${edge.target}#${(edgePage - 1) * TABLE_PAGE_SIZE + index}`} className="border-t border-border">
                  <td className={`${CELL} font-mono text-[12px]`}>{labels.get(edge.source) ?? edge.source}</td>
                  <td className={`${CELL} font-mono text-[12px]`}>{labels.get(edge.target) ?? edge.target}</td>
                  <td className={CELL}>{edge.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        {edges.length > TABLE_PAGE_SIZE ? (
          <Pager page={edgePage} size={TABLE_PAGE_SIZE} total={edges.length} noun={t("graphs.table.edgesNoun")} onPage={setEdgePage} />
        ) : null}
      </section>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";

import { Chip } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n";
import { bytes, ms, stamp } from "@/lib/format";
import { codegraphGraphQuery, codegraphQuery } from "@/lib/queries";
import type { CodeIndex } from "@/types/api";

import { CODE_GRAPH_LIMIT } from "./config";
import { GraphPanel } from "./GraphPanel";

const GRAPH_LEVEL = "module";

function Unavailable({ children }: { children: string }) {
  return (
    <Card>
      <EmptyState title={t("graphs.code.unavailable.title")}>{children}</EmptyState>
    </Card>
  );
}

function Loading() {
  return (
    <div role="status" aria-label={t("graphs.loading")}>
      <Skeleton className="h-96" />
    </div>
  );
}

function IndexList({ indexes, selected, onSelect }: { indexes: CodeIndex[]; selected: string; onSelect: (key: string) => void }) {
  return (
    <section>
      <SectionHead title={t("graphs.code.indexes")} />
      <ul className="grid gap-3">
        {indexes.map((index) => (
          <li key={index.key} className="min-w-0">
            <Card className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
              <div className="min-w-0">
                <h3 className="font-mono text-[13px] font-semibold wrap-anywhere">{index.key}</h3>
                <p className="font-mono text-xs text-muted-foreground wrap-anywhere">{index.path}</p>
                <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
                  <span>{t("graphs.code.size")}: {bytes(index.db_bytes)}</span>
                  <span>{t("graphs.code.synced")}: {stamp(index.last_sync)}</span>
                  {index.supported ? (
                    <>
                      <span>{t("graphs.code.files")}: {index.files}</span>
                      <span>{t("graphs.code.symbols")}: {index.symbols}</span>
                      <span>{t("graphs.code.edges")}: {index.edges}</span>
                    </>
                  ) : (
                    <Chip tone="dashed">{t("graphs.code.unsupportedBadge")}</Chip>
                  )}
                </p>
              </div>
              <Button
                size="sm"
                variant={index.key === selected ? "default" : "outline"}
                aria-pressed={index.key === selected}
                aria-label={`${t("graphs.code.select")}: ${index.key}`}
                onClick={() => onSelect(index.key)}
              >
                {index.key === selected ? t("graphs.code.selected") : t("graphs.code.select")}
              </Button>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SelectedGraph({ index }: { index: CodeIndex }) {
  const graph = useQuery({ ...codegraphGraphQuery(index.key, { level: GRAPH_LEVEL, limit: CODE_GRAPH_LIMIT }), enabled: index.supported });
  if (!index.supported) return <Unavailable>{t("graphs.code.unsupported")}</Unavailable>;
  if (graph.isError) {
    return (
      <Card>
        <ErrorState error={graph.error} onRetry={() => void graph.refetch()} />
      </Card>
    );
  }
  if (!graph.data) return <Loading />;
  const { data, meta } = graph.data;
  if (data.nodes.length === 0 && !meta.truncated) return <Unavailable>{t("graphs.code.emptyGraph")}</Unavailable>;
  return (
    <GraphPanel
      title={t("graphs.code.graphTitle", { key: index.key })}
      nodes={data.nodes}
      edges={data.edges}
      notice={
        <div role="status" className="mb-3 text-[12.5px] text-muted-foreground">
          {meta.truncated ? <p className="mb-1 rounded-md border border-dashed border-border-strong px-3 py-2">{t("graphs.code.truncated")}</p> : null}
          <p>{t("graphs.code.elapsed", { ms: ms(meta.elapsed_ms) })}</p>
        </div>
      }
    />
  );
}

export interface CodeGraphViewProps {
  /** The index chosen in the URL, if any. */
  indexKey: string | undefined;
  onSelect: (key: string) => void;
}

/** The code graph: the index list and the module-level graph of the selected index (FR-107). */
export function CodeGraphView({ indexKey, onSelect }: CodeGraphViewProps) {
  const indexes = useQuery(codegraphQuery());
  if (indexes.isError) {
    return (
      <Card>
        <ErrorState error={indexes.error} onRetry={() => void indexes.refetch()} />
      </Card>
    );
  }
  if (!indexes.data) return <Loading />;
  if (indexes.data.length === 0) return <Unavailable>{t("graphs.code.noIndex")}</Unavailable>;
  const selected = indexes.data.find((index) => index.key === indexKey) ?? indexes.data.find((index) => index.supported) ?? indexes.data[0];
  return (
    <div className="grid min-w-0 gap-6">
      <IndexList indexes={indexes.data} selected={selected.key} onSelect={onSelect} />
      <SelectedGraph key={selected.key} index={selected} />
    </div>
  );
}

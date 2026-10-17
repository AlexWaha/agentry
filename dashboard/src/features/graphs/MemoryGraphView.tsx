import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n";
import { memoryGraphQuery } from "@/lib/queries";

import { GraphPanel } from "./GraphPanel";

/** The memory graph: lessons and patterns linked to tasks, agents and areas (FR-106). */
export function MemoryGraphView() {
  const graph = useQuery(memoryGraphQuery());
  if (graph.isError) {
    return (
      <Card>
        <ErrorState error={graph.error} onRetry={() => void graph.refetch()} />
      </Card>
    );
  }
  if (!graph.data) {
    return (
      <div role="status" aria-label={t("graphs.loading")}>
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (graph.data.nodes.length === 0) {
    return (
      <Card>
        <EmptyState title={t("graphs.memory.empty.title")}>{t("graphs.memory.empty.body")}</EmptyState>
      </Card>
    );
  }
  return <GraphPanel title={t("graphs.memory.title")} nodes={graph.data.nodes} edges={graph.data.edges} />;
}

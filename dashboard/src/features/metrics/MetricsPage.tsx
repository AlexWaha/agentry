import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatCard } from "@/components/shared/StatCard";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n";
import { dur } from "@/lib/format";
import { metricsQuery } from "@/lib/queries";
import type { Metrics } from "@/types/api";

import { DEFAULT_RANGE, METRICS_RANGES, type MetricsRange } from "./config";
import { MetricsCharts, RejectionsCard } from "./MetricsCharts";
import type { MetricsSearch } from "./search";

const SKELETON_BLOCKS = 4;

function subtitleOf(metrics: Metrics): string {
  const [from, to] = metrics.span.split("/");
  return metrics.events_total === 0 ? t("metrics.subtitle.none") : t("metrics.subtitle", { events: metrics.events_total, from: from.slice(0, 10), to: to.slice(0, 10) });
}

export function MetricsPage() {
  const days: MetricsRange = useSearch({ strict: false }).days ?? DEFAULT_RANGE;
  const navigate = useNavigate();
  const query = useQuery(metricsQuery({ days }));
  const metrics = query.data;

  const setRange = (next: MetricsRange): void => {
    void navigate({ to: ".", search: (prev: MetricsSearch) => ({ ...prev, days: next === DEFAULT_RANGE ? undefined : next }) });
  };

  let body;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (!metrics) {
    body = (
      <div role="status" aria-label={t("metrics.loading")} className="grid grid-cols-[minmax(0,1fr)] gap-4 shell:grid-cols-2">
        {Array.from({ length: SKELETON_BLOCKS }, (_, index) => (
          <Skeleton key={index} className="h-40" />
        ))}
      </div>
    );
  } else if (metrics.events_total === 0) {
    body = <EmptyState title={t("metrics.empty.title")}>{t("metrics.empty.hint")}</EmptyState>;
  } else {
    body = (
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 shell:grid-cols-4">
          <StatCard label={t("metrics.stat.events")} value={metrics.events_total} />
          <StatCard label={t("metrics.stat.merges")} value={metrics.merges_total} tone="done" />
          <StatCard label={t("metrics.stat.leadTime")} value={dur(metrics.lead_time_s)} hint={t("metrics.stat.leadTime.hint")} />
          <StatCard label={t("metrics.stat.rejections")} value={metrics.rejections.total} />
        </div>
        <MetricsCharts metrics={metrics} />
        <RejectionsCard rejections={metrics.rejections} />
      </div>
    );
  }

  return (
    <>
      <PageHeader title={t("metrics.title")} subtitle={metrics ? subtitleOf(metrics) : undefined} docsPage="metrics" />
      <div className="mb-3.5">
        <Select aria-label={t("metrics.range.label")} value={String(days)} onChange={(event) => setRange(Number(event.target.value) as MetricsRange)}>
          {METRICS_RANGES.map((range) => (
            <option key={range} value={range}>
              {t("metrics.range.option", { days: range })}
            </option>
          ))}
        </Select>
      </div>
      {body}
    </>
  );
}

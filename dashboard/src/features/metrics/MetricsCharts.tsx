import { Link } from "@tanstack/react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { stageLabel } from "@/components/shared/Stepper";
import { Card } from "@/components/ui/card";
import { taskPath } from "@/config/routes";
import { t } from "@/i18n";
import { dur, ms, stamp } from "@/lib/format";
import type { Metrics } from "@/types/api";

import { BarChart } from "./BarChart";
import { ChartCard } from "./ChartCard";
import { SERIES_FILLS } from "./config";

const count = (value: number): string => String(value);
const dayLabel = (day: string): string => day.slice(5);

/** The five charts of the Metrics page, each with its table alternative (FR-101, FR-102). */
export function MetricsCharts({ metrics }: { metrics: Metrics }) {
  const gateStages = metrics.gates.map((gate) => stageLabel(gate.stage));
  const dwellStages = metrics.dwell.map((row) => stageLabel(row.stage));
  const days = metrics.per_day.map((row) => dayLabel(row.day));
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 shell:grid-cols-2">
      <ChartCard
        title={t("metrics.gateDuration.title")}
        description={t("metrics.gateDuration.desc")}
        empty={metrics.gates.length === 0}
        table={{
          head: [t("metrics.col.stage"), t("metrics.series.median"), t("metrics.series.p90"), t("metrics.series.max")],
          rows: metrics.gates.map((gate, index) => [gateStages[index], ms(gate.median_ms), ms(gate.p90_ms), ms(gate.max_ms)]),
        }}
      >
        {(ids) => (
          <BarChart
            {...ids}
            orientation="horizontal"
            categories={gateStages}
            format={ms}
            series={[
              { label: t("metrics.series.median"), fill: SERIES_FILLS[0], values: metrics.gates.map((gate) => gate.median_ms) },
              { label: t("metrics.series.p90"), fill: SERIES_FILLS[1], values: metrics.gates.map((gate) => gate.p90_ms) },
              { label: t("metrics.series.max"), fill: SERIES_FILLS[3], values: metrics.gates.map((gate) => gate.max_ms) },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title={t("metrics.gateFailures.title")}
        description={t("metrics.gateFailures.desc")}
        empty={metrics.gates.length === 0}
        table={{
          head: [t("metrics.col.stage"), t("metrics.series.runs"), t("metrics.series.failed")],
          rows: metrics.gates.map((gate, index) => [gateStages[index], gate.runs, gate.failures]),
        }}
      >
        {(ids) => (
          <BarChart
            {...ids}
            orientation="horizontal"
            categories={gateStages}
            format={count}
            series={[
              { label: t("metrics.series.runs"), fill: SERIES_FILLS[0], values: metrics.gates.map((gate) => gate.runs) },
              { label: t("metrics.series.failed"), fill: SERIES_FILLS[1], values: metrics.gates.map((gate) => gate.failures) },
            ]}
          />
        )}
      </ChartCard>

      <ChartCard
        title={t("metrics.slowest.title")}
        description={t("metrics.slowest.desc")}
        empty={metrics.slowest.length === 0}
        table={{
          head: [t("metrics.col.task"), t("metrics.col.stage"), t("metrics.series.duration"), t("metrics.col.result"), t("metrics.col.when")],
          rows: metrics.slowest.map((gate) => [
            gate.task,
            stageLabel(gate.stage),
            ms(gate.ms),
            t(gate.passed ? "metrics.result.pass" : "metrics.result.fail"),
            stamp(gate.at),
          ]),
        }}
      >
        {(ids) => (
          <BarChart
            {...ids}
            orientation="horizontal"
            categories={metrics.slowest.map((gate, index) => `${index + 1}. ${gate.task} ${stageLabel(gate.stage)}`)}
            format={ms}
            series={[{ label: t("metrics.series.duration"), fill: SERIES_FILLS[0], values: metrics.slowest.map((gate) => gate.ms) }]}
          />
        )}
      </ChartCard>

      <ChartCard
        title={t("metrics.dwell.title")}
        description={t("metrics.dwell.desc")}
        empty={metrics.dwell.length === 0}
        table={{
          head: [t("metrics.col.stage"), t("metrics.col.tasks"), t("metrics.series.median"), t("metrics.series.p90"), t("metrics.series.max")],
          rows: metrics.dwell.map((row, index) => [dwellStages[index], row.n, dur(row.median_s), dur(row.p90_s), dur(row.max_s)]),
        }}
      >
        {(ids) => (
          <BarChart
            {...ids}
            orientation="horizontal"
            categories={dwellStages}
            format={dur}
            series={[
              { label: t("metrics.series.median"), fill: SERIES_FILLS[0], values: metrics.dwell.map((row) => row.median_s) },
              { label: t("metrics.series.p90"), fill: SERIES_FILLS[1], values: metrics.dwell.map((row) => row.p90_s) },
              { label: t("metrics.series.max"), fill: SERIES_FILLS[3], values: metrics.dwell.map((row) => row.max_s) },
            ]}
          />
        )}
      </ChartCard>

      <div className="shell:col-span-2">
        <ChartCard
          title={t("metrics.perDay.title")}
          description={t("metrics.perDay.desc")}
          empty={metrics.per_day.length === 0}
          table={{
            head: [t("metrics.col.day"), t("metrics.series.gates"), t("metrics.series.failed"), t("metrics.series.merged"), t("metrics.series.rejected")],
            rows: metrics.per_day.map((row) => [row.day, row.gates, row.gate_failures, row.merged, row.rejected]),
          }}
        >
          {(ids) => (
            <BarChart
              {...ids}
              orientation="vertical"
              categories={days}
              format={count}
              series={[
                { label: t("metrics.series.gates"), fill: SERIES_FILLS[0], values: metrics.per_day.map((row) => row.gates) },
                { label: t("metrics.series.failed"), fill: SERIES_FILLS[1], values: metrics.per_day.map((row) => row.gate_failures) },
                { label: t("metrics.series.merged"), fill: SERIES_FILLS[2], values: metrics.per_day.map((row) => row.merged) },
                { label: t("metrics.series.rejected"), fill: SERIES_FILLS[3], values: metrics.per_day.map((row) => row.rejected) },
              ]}
            />
          )}
        </ChartCard>
      </div>
    </div>
  );
}

/** The latest rejections with the reason the reviewer or the User gave (FR-101). */
export function RejectionsCard({ rejections }: { rejections: Metrics["rejections"] }) {
  return (
    <Card className="min-w-0 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h2 className="text-[13px] font-semibold text-secondary-foreground">{t("metrics.rejections.title")}</h2>
        <span className="text-xs text-muted-foreground">{t("metrics.rejections.total", { total: rejections.total })}</span>
      </div>
      {rejections.latest.length === 0 ? (
        <EmptyState title={t("metrics.rejections.empty")} className="px-0 pb-0" />
      ) : (
        <ul className="m-0 mt-2 list-none p-0">
          {rejections.latest.map((row) => (
            <li key={`${row.task}-${row.at}`} className="border-b border-border py-2 text-[13px] last:border-b-0">
              <div className="flex flex-wrap items-baseline gap-x-3">
                <Link to={taskPath(row.task)} className="font-mono text-xs text-secondary-foreground underline-offset-2 hover:underline">
                  {row.task}
                </Link>
                <span className="text-xs text-muted-foreground">{t("metrics.rejections.from", { stage: stageLabel(row.stage) })}</span>
                <span className="font-mono text-xs text-muted-foreground">{stamp(row.at)}</span>
              </div>
              <p className="mt-0.5 text-[12.5px] wrap-anywhere text-muted-foreground">{row.reason || t("metrics.rejections.noReason")}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

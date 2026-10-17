import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { MemoryStats } from "@/types/api";

const BAR_WIDTH = 10;
const BAR_GAP = 4;
const CHART_HEIGHT = 80;
const MIN_BAR_HEIGHT = 2;

/** Rows added per day as SVG bars (FR-105). Height is relative to the busiest day; zero days show a stub. */
export function GrowthChart({ growth }: { growth: MemoryStats["growth"] }) {
  const peak = Math.max(1, ...growth.map((point) => point.rows));
  const total = growth.reduce((sum, point) => sum + point.rows, 0);
  const width = growth.length * (BAR_WIDTH + BAR_GAP);
  return (
    <Card className="px-4 py-3.5">
      <svg
        role="img"
        aria-label={t("memory.stats.growthLabel", { days: growth.length, total })}
        viewBox={`0 0 ${width} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        className="block h-24 w-full"
      >
        {growth.map((point, index) => {
          const height = point.rows === 0 ? MIN_BAR_HEIGHT : Math.max(MIN_BAR_HEIGHT, (point.rows / peak) * CHART_HEIGHT);
          return (
            <rect
              key={point.day}
              x={index * (BAR_WIDTH + BAR_GAP)}
              y={CHART_HEIGHT - height}
              width={BAR_WIDTH}
              height={height}
              className={point.rows === 0 ? "fill-border-strong" : "fill-status-active-fg"}
            >
              <title>{t("memory.stats.growthBar", { day: point.day, rows: point.rows })}</title>
            </rect>
          );
        })}
      </svg>
      <div className="mt-1.5 flex justify-between font-mono text-[11px] text-muted-foreground">
        <span>{growth[0]?.day}</span>
        <span>{growth[growth.length - 1]?.day}</span>
      </div>
    </Card>
  );
}

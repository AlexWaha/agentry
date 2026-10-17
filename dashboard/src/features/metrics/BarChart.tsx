import { BAR_RADIUS, HORIZONTAL, VERTICAL } from "./config";

export interface BarSeries {
  label: string;
  /** A Tailwind `fill-*` utility from a semantic colour token. */
  fill: string;
  /** One value per category, in category order. */
  values: number[];
}

export interface BarChartProps {
  /** Ids of the elements that name and describe the chart (FR-102). */
  labelledBy: string;
  describedBy: string;
  orientation: "horizontal" | "vertical";
  categories: string[];
  series: BarSeries[];
  format: (value: number) => string;
}

const ELLIPSIS = "...";

function shorten(label: string, limit: number): string {
  return label.length > limit ? `${label.slice(0, limit - ELLIPSIS.length)}${ELLIPSIS}` : label;
}

/** Bar length in SVG units: proportional to the value, never invisible for a value above zero. */
function length(value: number, max: number, span: number): number {
  return value > 0 ? Math.max(1, (value / max) * span) : 0;
}

function maxOf(series: BarSeries[]): number {
  return Math.max(1, ...series.flatMap((s) => s.values));
}

function Horizontal({ categories, series, format, ...aria }: Omit<BarChartProps, "orientation">) {
  const { width, labelWidth, valueWidth, barHeight, barGap, groupGap, labelMaxChars } = HORIZONTAL;
  const groupHeight = series.length * (barHeight + barGap) - barGap;
  const height = categories.length * (groupHeight + groupGap) - groupGap;
  const max = maxOf(series);
  const span = width - labelWidth - valueWidth;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-labelledby={aria.labelledBy}
      aria-describedby={aria.describedBy}
      focusable="false"
      className="block h-auto w-full"
    >
      {categories.map((category, index) => {
        const top = index * (groupHeight + groupGap);
        return (
          <g key={category}>
            <text x={labelWidth - 6} y={top + groupHeight / 2} textAnchor="end" dominantBaseline="central" className="fill-secondary-foreground text-[11px]">
              <title>{category}</title>
              {shorten(category, labelMaxChars)}
            </text>
            {series.map((s, position) => {
              const y = top + position * (barHeight + barGap);
              const size = length(s.values[index], max, span);
              return (
                <g key={s.label}>
                  <rect x={labelWidth} y={y} width={size} height={barHeight} rx={BAR_RADIUS} className={s.fill}>
                    <title>{`${category}, ${s.label}: ${format(s.values[index])}`}</title>
                  </rect>
                  <text x={labelWidth + size + 4} y={y + barHeight / 2} dominantBaseline="central" className="fill-muted-foreground text-[10px]">
                    {format(s.values[index])}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

function Vertical({ categories, series, format, ...aria }: Omit<BarChartProps, "orientation">) {
  const { width, height, axisWidth, top, bottom, slotGap, maxLabels } = VERTICAL;
  const plotHeight = height - top - bottom;
  const slot = (width - axisWidth) / categories.length;
  const barWidth = (slot * (1 - slotGap)) / series.length;
  const max = maxOf(series);
  const labelEvery = Math.ceil(categories.length / maxLabels);
  const baseline = top + plotHeight;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-labelledby={aria.labelledBy}
      aria-describedby={aria.describedBy}
      focusable="false"
      className="block h-auto w-full"
    >
      <line x1={axisWidth} x2={width} y1={top} y2={top} className="stroke-border" />
      <line x1={axisWidth} x2={width} y1={baseline} y2={baseline} className="stroke-border-strong" />
      <text x={axisWidth - 4} y={top} textAnchor="end" dominantBaseline="central" className="fill-muted-foreground text-[10px]">
        {format(max)}
      </text>
      <text x={axisWidth - 4} y={baseline} textAnchor="end" dominantBaseline="central" className="fill-muted-foreground text-[10px]">
        {format(0)}
      </text>
      {categories.map((category, index) => {
        const left = axisWidth + index * slot + (slot * slotGap) / 2;
        return (
          <g key={category}>
            {series.map((s, position) => {
              const size = length(s.values[index], max, plotHeight);
              return (
                <rect key={s.label} x={left + position * barWidth} y={baseline - size} width={barWidth} height={size} className={s.fill}>
                  <title>{`${category}, ${s.label}: ${format(s.values[index])}`}</title>
                </rect>
              );
            })}
            {index % labelEvery === 0 ? (
              <text x={axisWidth + index * slot + slot / 2} y={height - 5} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                {category}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

function Legend({ series }: { series: BarSeries[] }) {
  return (
    <ul className="m-0 mt-2 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-xs text-muted-foreground">
      {series.map((s) => (
        <li key={s.label} className="flex items-center gap-1.5">
          <svg aria-hidden="true" viewBox="0 0 10 10" className="size-2.5">
            <rect width="10" height="10" rx="2" className={s.fill} />
          </svg>
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** A plain SVG bar chart; every bar is one `rect` carrying its value in a `title`. Name and description come from the card (FR-102). */
export function BarChart({ orientation, ...props }: BarChartProps) {
  return (
    <>
      {orientation === "horizontal" ? <Horizontal {...props} /> : <Vertical {...props} />}
      {props.series.length > 1 ? <Legend series={props.series} /> : null}
    </>
  );
}

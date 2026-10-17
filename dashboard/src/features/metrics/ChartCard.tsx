import { useId, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";

export interface ChartTable {
  head: string[];
  rows: (string | number)[][];
}

export interface ChartIds {
  labelledBy: string;
  describedBy: string;
}

export interface ChartCardProps {
  title: string;
  description: string;
  /** The same numbers as the chart, shown by "Show as table" (FR-102). */
  table: ChartTable;
  /** True when there is nothing to chart: the card shows the EmptyState instead. */
  empty: boolean;
  children: (ids: ChartIds) => ReactNode;
}

/** A titled card around one chart: the name and description the svg refers to, and the table alternative. */
export function ChartCard({ title, description, table, empty, children }: ChartCardProps) {
  const base = useId();
  const [open, setOpen] = useState(false);
  const ids = { labelledBy: `${base}-title`, describedBy: `${base}-desc` };
  const tableId = `${base}-table`;
  return (
    <Card className="min-w-0 p-4">
      <h2 id={ids.labelledBy} className="text-[13px] font-semibold text-secondary-foreground">
        {title}
      </h2>
      <p id={ids.describedBy} className="mt-0.5 text-xs text-muted-foreground">
        {description}
      </p>
      {empty ? (
        <EmptyState title={t("metrics.chart.empty")} className="px-0 pb-0" />
      ) : (
        <>
          <div className="mt-3">{children(ids)}</div>
          <Button size="sm" variant="ghost" className="mt-2 -ml-2.5" aria-expanded={open} aria-controls={tableId} onClick={() => setOpen(!open)}>
            {open ? t("metrics.table.hide") : t("metrics.table.show")}
          </Button>
          <div id={tableId} hidden={!open} tabIndex={0} role="region" aria-label={t("metrics.table.label", { title })} className="mt-1 overflow-x-auto">
            <table className="w-full text-left text-[12.5px] tabular-nums">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  {table.head.map((cell) => (
                    <th key={cell} scope="col" className="py-1.5 pr-3 font-medium">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, index) => (
                  <tr key={index} className="border-b border-border last:border-b-0">
                    {row.map((cell, position) => (
                      <td key={position} className="py-1.5 pr-3 wrap-anywhere">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

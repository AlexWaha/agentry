import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface StatCardProps {
  label: string;
  value: ReactNode;
  /** A line under the value, for example a breakdown. */
  hint?: ReactNode;
  /** `done` tints the value green; `attention` is for the "Needs you" count only. */
  tone?: "default" | "done" | "attention";
  className?: string;
}

const VALUE_TONE: Record<NonNullable<StatCardProps["tone"]>, string> = {
  default: "text-foreground",
  done: "text-status-done-fg",
  attention: "text-attention",
};

export function StatCard({ label, value, hint, tone = "default", className }: StatCardProps) {
  return (
    <Card className={cn("min-w-0 px-4 pt-3.5 pb-4", className)}>
      <div className="text-[12.5px] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-[26px] font-semibold tracking-tight wrap-anywhere tabular-nums", VALUE_TONE[tone])}>{value}</div>
      {hint ? <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">{hint}</div> : null}
    </Card>
  );
}

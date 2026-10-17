import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface ChipProps extends Omit<HTMLAttributes<HTMLSpanElement>, "title"> {
  children: ReactNode;
  /**
   * `false` (default): a long value shrinks to an ellipsis and the full text goes into `title`.
   * `true`: the value wraps onto more lines and is never cut. Use it for hook matchers, command lines,
   * file paths and memory signatures, which must stay readable in full (FR-128).
   */
  wrap?: boolean;
  /** Overrides the tooltip; by default an ellipsized string child is its own title. */
  title?: string;
  tone?: "default" | "dashed";
}

/** A small pill for a type, epic, agent, matcher or signature. Never wider than its container. */
export function Chip({ children, wrap = false, title, tone = "default", className, ...props }: ChipProps) {
  const tooltip = title ?? (!wrap && typeof children === "string" ? children : undefined);
  return (
    <span
      title={tooltip}
      className={cn(
        "inline-block max-w-full min-w-0 rounded-full border px-2 align-middle font-mono text-[11px] leading-[18px] text-secondary-foreground",
        tone === "dashed" ? "border-dashed border-border-strong text-muted-foreground" : "border-border-strong",
        wrap ? "wrap-anywhere whitespace-normal" : "truncate",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

/** A row of chips that wraps instead of overflowing (FR-128). */
export function ChipRow({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex min-w-0 flex-wrap items-center gap-1.5", className)} {...props} />;
}

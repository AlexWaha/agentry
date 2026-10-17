import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** What is empty, in a few words. */
  title: string;
  /** The reason, and the way out if there is one. */
  children?: ReactNode;
  className?: string;
}

/** The empty state of a list or page: always says why nothing is shown (FR-89). */
export function EmptyState({ title, children, className }: EmptyStateProps) {
  return (
    <div className={cn("px-4 py-5 text-muted-foreground", className)}>
      <b className="font-medium text-foreground">{title}</b>
      {children ? <> {children}</> : null}
    </div>
  );
}

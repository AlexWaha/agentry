import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A pulsing placeholder block for the loading state (FR-89). Decorative: hidden from assistive tech. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />;
}

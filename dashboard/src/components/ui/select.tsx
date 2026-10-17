import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A native select (keyboard and screen-reader support for free) with the app's border and a chevron. */
export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={cn("relative inline-flex min-w-0", className)}>
      <select
        className="h-9 w-full min-w-0 appearance-none rounded-md border border-border-control bg-card py-0 pr-8 pl-3 text-[13.5px] text-foreground"
        {...props}
      >
        {children}
      </select>
      <ChevronDown aria-hidden="true" className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
    </span>
  );
}

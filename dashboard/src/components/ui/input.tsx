import type { InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-border-control bg-card px-3 text-[13.5px] text-foreground placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

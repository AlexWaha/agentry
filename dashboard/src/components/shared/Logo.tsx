import { cn } from "@/lib/utils";

export interface LogoProps {
  /** Pixel size of the square icon. */
  size?: number;
  className?: string;
}

/**
 * The Agentry icon: an orange push-button in a thin metal bezel (FR-86). Decorative: the product name is
 * written next to it. public/favicon.svg is the same artwork as a standalone file.
 */
export function Logo({ size = 28, className }: LogoProps) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false" className={cn("shrink-0", className)}>
      <rect x="4" y="4" width="56" height="56" rx="12" strokeWidth="2" className="fill-none stroke-logo-bezel" />
      <rect x="10" y="12" width="44" height="43" rx="9" className="fill-logo-shadow" />
      <rect x="10" y="9" width="44" height="43" rx="9" className="fill-logo-face" />
    </svg>
  );
}

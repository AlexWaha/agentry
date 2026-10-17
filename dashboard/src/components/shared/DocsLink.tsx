import { ExternalLink } from "lucide-react";

import { docsUrl } from "@/config/docs-links";
import type { PageId } from "@/config/routes";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export interface DocsLinkProps {
  /** The page whose docs-links.json entry the link opens. */
  page: PageId;
  label?: string;
  className?: string;
}

/** "Learn more" link to the GitHub documentation of a page; opens in a new tab (FR-112). */
export function DocsLink({ page, label, className }: DocsLinkProps) {
  return (
    <a
      href={docsUrl(page)}
      target="_blank"
      rel="noopener noreferrer"
      className={cn("inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground", className)}
    >
      {label ?? t("docs.learnMore")}
      <ExternalLink aria-hidden="true" className="size-3" />
    </a>
  );
}

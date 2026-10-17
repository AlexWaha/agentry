import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PAGER_FULL_LIMIT } from "@/config/constants";
import { t } from "@/i18n";
import { lastPage } from "@/lib/search-params";
import { cn } from "@/lib/utils";

/** The page links to show: every page up to PAGER_FULL_LIMIT, else 1, the neighbours of `page`, and the last, with `null` for a gap. */
export function pageWindow(page: number, last: number): (number | null)[] {
  if (last <= PAGER_FULL_LIMIT) return Array.from({ length: last }, (_, index) => index + 1);
  const keep = new Set([1, last, page - 1, page, page + 1].filter((n) => n >= 1 && n <= last));
  const out: (number | null)[] = [];
  let previous = 0;
  for (const n of [...keep].sort((a, b) => a - b)) {
    if (n - previous > 1) out.push(null);
    out.push(n);
    previous = n;
  }
  return out;
}

export interface PagerProps {
  /** Current page, 1-based. */
  page: number;
  /** Rows per page. */
  size: number;
  /** Exact total of the filtered list (`meta.total`). */
  total: number;
  /** What the rows are, in the plural: "tasks", "events". */
  noun: string;
  onPage: (page: number) => void;
  className?: string;
}

/**
 * The one pager of every paged list (FR-124): "26-50 of 120 tasks", Previous and Next (disabled at the ends)
 * and numbered pages with `aria-current="page"`. Hidden when the list is empty. Wraps on narrow widths.
 */
export function Pager({ page, size, total, noun, onPage, className }: PagerProps) {
  if (total <= 0) return null;
  const last = lastPage(total, size);
  const from = (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  return (
    <nav
      aria-label={t("pager.label", { noun })}
      className={cn("mt-3.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2.5 text-[12.5px] text-muted-foreground", className)}
    >
      <span role="status" className="tabular-nums">
        {t("pager.range", { from, to, total, noun })}
      </span>
      {last > 1 ? (
        <div className="flex flex-wrap items-center gap-1">
          <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
            <ChevronLeft aria-hidden="true" className="size-3.5" />
            {t("pager.previous")}
          </Button>
          {pageWindow(page, last).map((n, index) =>
            n === null ? (
              <span key={`gap-${index}`} aria-hidden="true" className="px-1">
                {t("pager.gap")}
              </span>
            ) : (
              <Button
                key={n}
                size="sm"
                variant={n === page ? "default" : "ghost"}
                className="min-w-7 px-1.5 tabular-nums"
                aria-label={t("pager.page", { page: n })}
                aria-current={n === page ? "page" : undefined}
                onClick={() => onPage(n)}
              >
                {n}
              </Button>
            ),
          )}
          <Button size="sm" disabled={page >= last} onClick={() => onPage(page + 1)}>
            {t("pager.next")}
            <ChevronRight aria-hidden="true" className="size-3.5" />
          </Button>
        </div>
      ) : null}
    </nav>
  );
}

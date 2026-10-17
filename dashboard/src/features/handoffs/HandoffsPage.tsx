import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { Pager } from "@/components/shared/Pager";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PAGE_SIZE } from "@/config/constants";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { bytes } from "@/lib/format";
import { handoffsQuery } from "@/lib/queries";
import { pageOffset, useClampPage, useListSearch, type PageSearch } from "@/lib/search-params";
import type { Handoff } from "@/types/api";

import { ValidityChip } from "./ValidityChip";

function HandoffRow({ handoff }: { handoff: Handoff }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border px-4 py-2.5 first:border-t-0">
      <Link to={ROUTES.handoff} params={{ taskId: handoff.task }} className="font-mono font-semibold underline underline-offset-2">
        {handoff.task}
      </Link>
      <span className="min-w-0 flex-1 basis-60 wrap-anywhere">{handoff.title || t("handoffs.untitled")}</span>
      <span className="min-w-0 text-muted-foreground wrap-anywhere">{t("handoffs.by", { author: handoff.author })}</span>
      <time className="text-muted-foreground tabular-nums" dateTime={handoff.date}>
        {handoff.date}
      </time>
      <span className="text-muted-foreground tabular-nums">{bytes(handoff.bytes)}</span>
      <ValidityChip valid={handoff.valid} />
    </li>
  );
}

/** Handoffs (FR-111): the documents newest first, 25 per page, each linking to its reader. */
export function HandoffsPage() {
  const { page, setPage } = useListSearch<PageSearch>();
  const size = PAGE_SIZE.handoffs;
  const query = useQuery(handoffsQuery({ limit: size, offset: pageOffset(page, size) }));
  const total = query.data?.meta.total;
  useClampPage(page, total, size, setPage);

  let body: ReactNode;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (query.data === undefined) {
    body = (
      <div role="status" aria-label={t("handoffs.loading")} className="grid gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  } else if (query.data.meta.total === 0) {
    body = (
      <Card>
        <EmptyState title={t("handoffs.empty")}>{t("handoffs.emptyHint")}</EmptyState>
      </Card>
    );
  } else {
    body = (
      <>
        <Card className="min-w-0">
          <ul aria-label={t("handoffs.list.label")}>
            {query.data.data.map((handoff) => (
              <HandoffRow key={handoff.task} handoff={handoff} />
            ))}
          </ul>
        </Card>
        <Pager page={page} size={size} total={query.data.meta.total} noun={t("handoffs.noun")} onPage={setPage} />
      </>
    );
  }

  return (
    <>
      <PageHeader title={t("handoffs.title")} subtitle={t("handoffs.subtitle")} docsPage="handoffs" />
      {body}
    </>
  );
}

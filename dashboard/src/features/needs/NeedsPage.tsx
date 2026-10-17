import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusChip } from "@/components/shared/StatusChip";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { ago, stamp } from "@/lib/format";
import { runsQuery } from "@/lib/queries";
import type { NeedsYou, Run } from "@/types/api";

/** The chip text of a checkpoint kind; a kind the dashboard does not know shows as the raw name. */
function kindLabel(kind: string): string {
  const key = `needs.kind.${kind}`;
  const label = t(key);
  return label === key ? kind : label;
}

function NeedsRow({ run, needs }: { run: Run; needs: NeedsYou }) {
  return (
    <li className="grid min-w-0 gap-1.5 border-b border-l-[3px] border-b-border border-l-attention px-4 py-3.5 last:border-b-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
        <StatusChip status="need" label={kindLabel(needs.kind)} />
        <Link
          to={ROUTES.task}
          params={{ taskId: run.task }}
          className="font-mono text-xs text-foreground underline underline-offset-2"
        >
          {run.task}
        </Link>
        <span className="min-w-0 font-medium wrap-anywhere">{run.title}</span>
      </div>
      {needs.reason ? <p className="m-0 text-secondary-foreground wrap-anywhere">{needs.reason}</p> : null}
      <p className="m-0 text-xs text-muted-foreground">
        {t("needs.since", { at: stamp(needs.since) })} ({ago(needs.since)})
      </p>
    </li>
  );
}

function Loading() {
  return (
    <div role="status" aria-busy="true" className="grid gap-3">
      <span className="sr-only">{t("needs.loading")}</span>
      <Skeleton className="h-20" />
      <Skeleton className="h-20" />
    </div>
  );
}

export function NeedsPage() {
  const runs = useQuery(runsQuery());

  let body: ReactNode;
  if (runs.isError) {
    body = (
      <Card>
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      </Card>
    );
  } else if (!runs.data) {
    body = <Loading />;
  } else {
    const waiting = runs.data
      .flatMap((run) => (run.needs_you ? [{ run, needs: run.needs_you }] : []))
      .sort((a, b) => a.needs.since.localeCompare(b.needs.since));
    body =
      waiting.length > 0 ? (
        <Card>
          <ul aria-label={t("needs.list")} className="m-0 list-none p-0">
            {waiting.map(({ run, needs }) => (
              <NeedsRow key={`${run.lane}:${run.task}`} run={run} needs={needs} />
            ))}
          </ul>
        </Card>
      ) : (
        <Card>
          <EmptyState title={t("needs.empty.title")}>{t("needs.empty.body")}</EmptyState>
        </Card>
      );
  }

  return (
    <>
      <PageHeader title={t("needs.title")} subtitle={t("needs.subtitle")} docsPage="needs" />
      {body}
    </>
  );
}

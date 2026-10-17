import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";

import { describeEvent } from "@/components/shared/EventTimeline";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader, SectionHead } from "@/components/shared/PageHeader";
import { StatCard } from "@/components/shared/StatCard";
import { Stepper } from "@/components/shared/Stepper";
import { StatusChip } from "@/components/shared/StatusChip";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES, taskPath } from "@/config/routes";
import { t } from "@/i18n";
import { stamp } from "@/lib/format";
import { applyAppended, type CachedEvents } from "@/lib/live-events";
import { eventsQuery, runsQuery, statusQuery } from "@/lib/queries";
import { onEventsAppended } from "@/lib/ws-client";
import type { Event, Run, Status } from "@/types/api";

const LATEST_EVENTS = 5;
const TASK_LINK = "font-mono text-xs text-foreground underline underline-offset-2";

function Loading() {
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">{t("overview.loading")}</span>
      <div className="grid grid-cols-2 gap-3.5 shell:grid-cols-4">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-24" />
        ))}
      </div>
      <Skeleton className="mt-6 h-40" />
    </div>
  );
}

function RunBlock({ run }: { run: Run }) {
  return (
    <li className="grid min-w-0 gap-3 border-b border-border px-4 py-3.5 last:border-b-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
        <Link to={ROUTES.task} params={{ taskId: run.task }} className={TASK_LINK}>
          {run.task}
        </Link>
        <span className="min-w-0 font-medium wrap-anywhere">{run.title}</span>
        {run.needs_you ? <StatusChip status="need" label={t("overview.runs.needsYou")} /> : null}
      </div>
      <Stepper stages={run.stages} blocked={run.stage_status === "blocked"} needsYou={run.needs_you !== null} />
    </li>
  );
}

function EventRow({ event }: { event: Event }) {
  return (
    <li className="grid grid-cols-[minmax(0,1fr)] gap-x-3 gap-y-0.5 border-b border-border px-4 py-2 text-[13px] last:border-b-0 shell:grid-cols-[7rem_6rem_minmax(0,1fr)]">
      <span className="font-mono text-xs text-muted-foreground">{stamp(event.at)}</span>
      <span className="min-w-0">
        {event.task ? (
          <Link to={taskPath(event.task)} className={TASK_LINK}>
            {event.task}
          </Link>
        ) : null}
      </span>
      <span className="min-w-0 wrap-anywhere">{describeEvent(event).title}</span>
    </li>
  );
}

function OverviewData({ status, runs, events }: { status: Status; runs: Run[]; events: Event[] }) {
  const needing = runs.filter((run) => run.needs_you !== null).length;
  const { counts, debt } = status;

  return (
    <>
      <section aria-label={t("overview.counts")} className="grid grid-cols-2 gap-3.5 shell:grid-cols-4">
        <StatCard label={t("overview.stat.backlog")} value={counts.backlog} />
        <StatCard label={t("overview.stat.active")} value={counts.active} />
        <StatCard label={t("overview.stat.done")} value={counts.done} tone="done" />
        <StatCard
          label={t("overview.stat.needs")}
          value={needing}
          tone={needing > 0 ? "attention" : "default"}
          hint={
            <Link to={ROUTES.needs} className="underline underline-offset-2">
              {t("overview.stat.needsLink")}
            </Link>
          }
        />
      </section>

      <section className="mt-6">
        <SectionHead title={t("overview.runs.title")} right={t("overview.runs.count", { count: runs.length })} />
        <Card>
          {runs.length > 0 ? (
            <ul className="m-0 list-none p-0">
              {runs.map((run) => (
                <RunBlock key={`${run.lane}:${run.task}`} run={run} />
              ))}
            </ul>
          ) : (
            <EmptyState title={t("overview.runs.empty.title")}>{t("overview.runs.empty.body")}</EmptyState>
          )}
        </Card>
      </section>

      <section className="mt-6">
        <SectionHead title={t("overview.events.title")} />
        <Card>
          {events.length > 0 ? (
            <ol aria-label={t("overview.events.label")} className="m-0 list-none p-0">
              {events.map((event) => (
                <EventRow key={event.id} event={event} />
              ))}
            </ol>
          ) : (
            <EmptyState title={t("overview.events.empty.title")}>{t("overview.events.empty.body")}</EmptyState>
          )}
        </Card>
      </section>

      <section className="mt-6">
        <SectionHead title={t("overview.debt.title")} />
        <Card className="flex flex-wrap gap-x-6 gap-y-2 px-4 py-3.5">
          <Link to={ROUTES.handoffs} className="text-foreground underline underline-offset-2">
            {t("overview.debt.handoffs", { count: debt.handoffs.length })}
          </Link>
          <Link to={ROUTES.handoffs} className="text-foreground underline underline-offset-2">
            {t("overview.debt.memory", { count: debt.memory.length })}
          </Link>
        </Card>
      </section>
    </>
  );
}

const latestEventsQuery = () => eventsQuery({ limit: LATEST_EVENTS });

/** Keeps the cached latest-events list current from `events.appended` (FR-99). */
function useLiveLatestEvents(): void {
  const queryClient = useQueryClient();
  useEffect(
    () =>
      onEventsAppended((rows) => {
        const { queryKey } = latestEventsQuery();
        queryClient.setQueryData<CachedEvents>(queryKey, (cached) =>
          cached ? applyAppended(cached, { limit: LATEST_EVENTS }, rows) : cached,
        );
      }),
    [queryClient],
  );
}

export function OverviewPage() {
  const status = useQuery(statusQuery());
  const runs = useQuery(runsQuery());
  // Rows that arrive while the page is closed are not patched into the cache, so a visit starts with a fresh answer.
  const events = useQuery({ ...latestEventsQuery(), refetchOnMount: "always" });
  useLiveLatestEvents();
  const failed = [status, runs, events].filter((query) => query.isError);

  let body: ReactNode;
  if (failed.length > 0) {
    body = (
      <Card>
        <ErrorState
          error={failed[0].error}
          onRetry={() => {
            for (const query of failed) void query.refetch();
          }}
        />
      </Card>
    );
  } else if (!status.data || !runs.data || !events.data) {
    body = <Loading />;
  } else {
    body = <OverviewData status={status.data} runs={runs.data} events={events.data.data} />;
  }

  return (
    <>
      <PageHeader title={t("overview.title")} subtitle={t("overview.subtitle")} docsPage="overview" />
      {body}
    </>
  );
}

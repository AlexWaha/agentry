import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Search } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { describeEvent } from "@/components/shared/EventTimeline";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { Pager } from "@/components/shared/Pager";
import { StatusChip, type StatusKind } from "@/components/shared/StatusChip";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PAGE_SIZE } from "@/config/constants";
import type { EventsParams } from "@/config/query-keys";
import { taskPath } from "@/config/routes";
import { EVENT_KIND_KEYS } from "@/config/stages";
import { t } from "@/i18n";
import { stamp } from "@/lib/format";
import { eventsQuery } from "@/lib/queries";
import { enumParam, hasFilters, pageOffset, useClampPage, useListSearch, useSearchText } from "@/lib/search-params";
import { cn } from "@/lib/utils";
import type { Event } from "@/types/api";

import { useLiveEvents } from "./live";
import { ACTIVITY_FILTER_KEYS, RESULT_VALUES, type ActivitySearch } from "./search";

const SKELETON_ROWS = 6;

/** The status colour an event kind leads to; orange (`need`) only where a human has to act. */
const KIND_STATUS: Record<string, StatusKind> = {
  gate: "backlog",
  rejected: "backlog",
  parked: "need",
  blocked: "need",
  approved: "done",
  merged: "done",
};

function kindLabel(kind: string): string {
  const key = EVENT_KIND_KEYS[kind];
  return key ? t(key) : kind;
}

function EventRow({ event }: { event: Event }) {
  const row = describeEvent(event);
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-4 py-2.5 text-[13px] last:border-b-0 shell:grid shell:grid-cols-[8rem_6.5rem_7rem_minmax(0,1fr)] shell:items-center">
      <span className="font-mono text-xs text-muted-foreground">{stamp(event.at)}</span>
      {event.task ? (
        <Link to={taskPath(event.task)} className="min-w-0 font-mono text-xs wrap-anywhere text-secondary-foreground underline-offset-2 hover:underline">
          {event.task}
        </Link>
      ) : (
        <span aria-hidden="true" />
      )}
      <StatusChip status={KIND_STATUS[event.kind] ?? "active"} label={kindLabel(event.kind)} />
      <span className={cn("min-w-0 basis-full wrap-anywhere shell:basis-auto", (row.tone === "fail" || row.tone === "reject") && "font-semibold")}>
        {row.title}
        {row.note ? <span className="ml-2 text-xs font-normal text-muted-foreground">{row.note}</span> : null}
        {row.reason ? <span className="mt-0.5 block text-[12.5px] font-normal text-muted-foreground">{row.reason}</span> : null}
      </span>
    </li>
  );
}

export function ActivityPage() {
  const { search, page, setPage, setFilters, clearFilters } = useListSearch<ActivitySearch>();
  const params: EventsParams = {
    task: search.task,
    kind: search.kind,
    result: search.result,
    limit: PAGE_SIZE.events,
    offset: pageOffset(page, PAGE_SIZE.events),
  };
  // Rows that arrive while the page is closed are not patched into the cache, so a visit starts with a fresh answer.
  const query = useQuery({ ...eventsQuery(params), refetchOnMount: "always" });
  useLiveEvents();
  useClampPage(page, query.data?.meta.total, PAGE_SIZE.events, setPage);
  const [taskText, setTaskText] = useSearchText(search.task ?? "", (value) =>
    setFilters({ task: value.trim() || undefined }, { replace: true }),
  );

  const kinds = query.data?.meta.kinds ?? [];
  const kindOptions = search.kind && !kinds.includes(search.kind) ? [...kinds, search.kind] : kinds;
  const events = query.data?.data ?? [];

  let body;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (query.isPending) {
    body = (
      <div role="status" aria-label={t("activity.loading")} className="space-y-2 p-4">
        {Array.from({ length: SKELETON_ROWS }, (_, index) => (
          <Skeleton key={index} className="h-5" />
        ))}
      </div>
    );
  } else if (events.length === 0) {
    body = hasFilters(search, ACTIVITY_FILTER_KEYS) ? (
      <EmptyState title={t("activity.empty.filtered.title")}>{t("activity.empty.filtered.hint")}</EmptyState>
    ) : (
      <EmptyState title={t("activity.empty.none.title")}>{t("activity.empty.none.hint")}</EmptyState>
    );
  } else {
    body = (
      <ol aria-label={t("activity.feed.label")} className="m-0 list-none p-0">
        {events.map((event) => (
          <EventRow key={event.id} event={event} />
        ))}
      </ol>
    );
  }

  return (
    <>
      <PageHeader title={t("activity.title")} subtitle={t("activity.subtitle")} docsPage="activity" />
      <div role="group" aria-label={t("activity.filters.label")} className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-40 flex-1 basis-56 max-w-72">
          <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            className="pl-8"
            placeholder={t("activity.filter.task.placeholder")}
            aria-label={t("activity.filter.task.label")}
            value={taskText}
            onChange={(event) => setTaskText(event.target.value)}
          />
        </div>
        <Select
          aria-label={t("activity.filter.kind.label")}
          value={search.kind ?? ""}
          onChange={(event) => setFilters({ kind: event.target.value || undefined })}
        >
          <option value="">{t("activity.filter.kind.all")}</option>
          {kindOptions.map((kind) => (
            <option key={kind} value={kind}>
              {kindLabel(kind)}
            </option>
          ))}
        </Select>
        <Select
          aria-label={t("activity.filter.result.label")}
          value={search.result ?? ""}
          onChange={(event) => setFilters({ result: enumParam(event.target.value, RESULT_VALUES) })}
        >
          <option value="">{t("activity.filter.result.any")}</option>
          <option value="pass">{t("activity.filter.result.pass")}</option>
          <option value="fail">{t("activity.filter.result.fail")}</option>
        </Select>
        {hasFilters(search, ACTIVITY_FILTER_KEYS) ? (
          <Button variant="ghost" onClick={() => clearFilters(ACTIVITY_FILTER_KEYS)}>
            {t("activity.clear")}
          </Button>
        ) : null}
      </div>
      <Card>{body}</Card>
      {query.data ? <Pager page={page} size={PAGE_SIZE.events} total={query.data.meta.total} noun={t("activity.noun")} onPage={setPage} /> : null}
    </>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Search } from "lucide-react";
import type { ReactNode } from "react";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { Pager } from "@/components/shared/Pager";
import { StatusChip } from "@/components/shared/StatusChip";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PAGE_SIZE } from "@/config/constants";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { epicsQuery, tasksQuery } from "@/lib/queries";
import { hasFilters, pageOffset, useClampPage, useListSearch, useSearchText } from "@/lib/search-params";
import type { TaskSummary } from "@/types/api";

import { FILTER_KEYS, FOLDERS, type TasksSearch } from "./search";

const LINK = "text-foreground underline underline-offset-2";

/** The options of a filter select; the value in the URL stays selectable even when the data no longer lists it. */
function withCurrent(options: string[], current: string | undefined): string[] {
  return current && !options.includes(current) ? [...options, current] : options;
}

function TaskRow({ task }: { task: TaskSummary }) {
  return (
    <li className="grid min-w-0 grid-cols-[minmax(0,1fr)] items-start gap-x-3 gap-y-1.5 border-b border-border px-4 py-3 last:border-b-0 shell:grid-cols-[6rem_6.5rem_minmax(0,1fr)_minmax(0,16rem)]">
      <span>
        <StatusChip status={task.folder} />
      </span>
      <Link to={ROUTES.task} params={{ taskId: task.id }} className={`${LINK} font-mono text-xs wrap-anywhere`}>
        {task.id}
      </Link>
      <Link to={ROUTES.task} params={{ taskId: task.id }} className="line-clamp-2 min-w-0 font-medium wrap-anywhere text-foreground">
        {task.title}
      </Link>
      <ChipRow className="shell:justify-end">
        {task.priority === "high" ? <Chip>{t("tasks.row.high")}</Chip> : null}
        {task.type ? <Chip>{task.type}</Chip> : null}
        {task.epic ? <Chip>{task.epic}</Chip> : null}
        {task.superseded_by ? <Chip tone="dashed">{t("tasks.row.supersededBy", { task: task.superseded_by })}</Chip> : null}
      </ChipRow>
    </li>
  );
}

function Loading() {
  return (
    <div role="status" aria-busy="true" className="grid gap-px p-2">
      <span className="sr-only">{t("tasks.loading")}</span>
      {[0, 1, 2, 3, 4].map((n) => (
        <Skeleton key={n} className="h-12" />
      ))}
    </div>
  );
}

export function TasksPage() {
  const { search, page, setPage, setFilters, clearFilters } = useListSearch<TasksSearch>();
  const tasks = useQuery(
    tasksQuery({
      folder: search.folder,
      type: search.type,
      epic: search.epic,
      q: search.q,
      include_superseded: search.superseded ? true : undefined,
      limit: PAGE_SIZE.tasks,
      offset: pageOffset(page, PAGE_SIZE.tasks),
    }),
  );
  const epics = useQuery(epicsQuery());
  const meta = tasks.data?.meta;
  const [text, setText] = useSearchText(search.q ?? "", (value) => setFilters({ q: value.trim() || undefined }, { replace: true }));
  useClampPage(page, meta?.total, PAGE_SIZE.tasks, setPage);

  let list: ReactNode;
  if (tasks.isError) {
    list = <ErrorState error={tasks.error} onRetry={() => void tasks.refetch()} />;
  } else if (!tasks.data) {
    list = <Loading />;
  } else if (tasks.data.data.length === 0) {
    list = <EmptyState title={t("tasks.empty.title")}>{t("tasks.empty.body")}</EmptyState>;
  } else {
    list = (
      <ul aria-label={t("tasks.list")} className="m-0 list-none p-0">
        {tasks.data.data.map((task) => (
          <TaskRow key={task.id} task={task} />
        ))}
      </ul>
    );
  }

  const hidden = meta?.hidden_superseded ?? 0;
  const epicIds = withCurrent((epics.data ?? []).map((epic) => epic.id), search.epic);
  const epicTitles = new Map((epics.data ?? []).map((epic) => [epic.id, epic.title]));

  return (
    <>
      <PageHeader title={t("tasks.title")} subtitle={t("tasks.subtitle")} docsPage="tasks" />
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2.5">
        <div className="relative min-w-48 flex-1 shell:max-w-sm">
          <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            className="pl-8"
            placeholder={t("tasks.search.placeholder")}
            aria-label={t("tasks.search.label")}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <Select
          aria-label={t("tasks.filter.folder")}
          value={search.folder ?? ""}
          onChange={(event) => setFilters({ folder: FOLDERS.find((folder) => folder === event.target.value) })}
        >
          <option value="">{t("tasks.filter.allFolders")}</option>
          {FOLDERS.map((folder) => {
            const name = t(`chip.status.${folder}`);
            const count = meta?.facets.folders[folder];
            return (
              <option key={folder} value={folder}>
                {count === undefined ? name : t("tasks.filter.folderOption", { folder: name, count })}
              </option>
            );
          })}
        </Select>
        <Select aria-label={t("tasks.filter.type")} value={search.type ?? ""} onChange={(event) => setFilters({ type: event.target.value || undefined })}>
          <option value="">{t("tasks.filter.allTypes")}</option>
          {withCurrent(meta?.facets.types ?? [], search.type).map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </Select>
        <Select aria-label={t("tasks.filter.epic")} value={search.epic ?? ""} onChange={(event) => setFilters({ epic: event.target.value || undefined })}>
          <option value="">{t("tasks.filter.allEpics")}</option>
          {epicIds.map((id) => (
            <option key={id} value={id} title={epicTitles.get(id)}>
              {id}
            </option>
          ))}
        </Select>
        {hasFilters(search, FILTER_KEYS) ? (
          <Button size="sm" onClick={() => clearFilters(FILTER_KEYS)}>
            {t("tasks.filter.clear")}
          </Button>
        ) : null}
        {hidden > 0 || search.superseded ? (
          <div className="flex flex-wrap items-center gap-x-3 text-[12.5px] text-muted-foreground">
            <span>{t("tasks.superseded.hidden", { count: hidden })}</span>
            <label className="inline-flex items-center gap-1.5 text-foreground">
              <input
                type="checkbox"
                checked={search.superseded === 1}
                onChange={(event) => setFilters({ superseded: event.target.checked ? 1 : undefined })}
                className="size-4 accent-status-active-edge"
              />
              {t("tasks.superseded.toggle")}
            </label>
          </div>
        ) : null}
      </div>
      <Card>{list}</Card>
      {meta ? <Pager page={page} size={PAGE_SIZE.tasks} total={meta.total} noun={t("tasks.noun")} onPage={setPage} /> : null}
    </>
  );
}

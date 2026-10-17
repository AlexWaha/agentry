import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { ErrorState } from "@/components/shared/ErrorState";
import { NotFound } from "@/components/shared/NotFound";
import { SectionHead } from "@/components/shared/PageHeader";
import { StatusChip, statusOfFolder } from "@/components/shared/StatusChip";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { ApiError } from "@/lib/api-client";
import { capitalize } from "@/lib/format";
import { taskQuery } from "@/lib/queries";
import type { TaskDetail, TaskRef } from "@/types/api";

import { TaskBody } from "./TaskBody";
import { TaskPipeline } from "./TaskPipeline";

const HTTP_NOT_FOUND = 404;
const HTTP_INVALID = 422;
const LINK = "text-foreground underline underline-offset-2";

function BackLink() {
  return (
    <Link to={ROUTES.tasks} className="mb-3 inline-flex items-center gap-1 text-[12.5px] text-muted-foreground hover:text-foreground">
      <ChevronLeft aria-hidden="true" className="size-3.5" />
      {t("task.back")}
    </Link>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="m-0 mt-0.5 min-w-0 wrap-anywhere">{children}</dd>
    </div>
  );
}

const orNone = (value: string): ReactNode => (value ? value : t("task.meta.none"));

function TaskLinks({ refs }: { refs: TaskRef[] }) {
  return refs.length > 0 ? (
    <ChipRow>
      {refs.map((ref) => (
        <Link key={ref.id} to={ROUTES.task} params={{ taskId: ref.id }} className="inline-block max-w-full min-w-0" title={ref.title}>
          <Chip>{ref.id}</Chip>
        </Link>
      ))}
    </ChipRow>
  ) : (
    t("task.meta.none")
  );
}

function TaskHeader({ task }: { task: TaskDetail }) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
        <span className="font-mono text-xs text-muted-foreground">{task.id}</span>
        <StatusChip status={statusOfFolder(task.folder)} />
        {task.run?.needs_you ? <StatusChip status="need" label={t("task.needsYou", { kind: task.run.needs_you.kind })} /> : null}
        {task.superseded_by ? <Chip tone="dashed">{t("task.superseded", { task: task.superseded_by })}</Chip> : null}
      </div>
      <h1 className="mt-1.5 text-[22px] font-semibold tracking-tight wrap-anywhere">{task.title}</h1>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-[13px] shell:grid-cols-4">
        <Meta label={t("task.meta.type")}>{task.type ? <Chip>{task.type}</Chip> : t("task.meta.none")}</Meta>
        <Meta label={t("task.meta.priority")}>{task.priority ? capitalize(task.priority) : t("task.meta.none")}</Meta>
        <Meta label={t("task.meta.epic")}>{task.epic ? <Chip>{task.epic}</Chip> : t("task.meta.none")}</Meta>
        <Meta label={t("task.meta.spec")}>{task.spec ? <Chip>{task.spec}</Chip> : t("task.meta.none")}</Meta>
        <Meta label={t("task.meta.assignee")}>{task.assignee ? <span className="font-mono text-xs">{task.assignee}</span> : t("task.meta.none")}</Meta>
        <Meta label={t("task.meta.dependsOn")}>
          <TaskLinks refs={task.depends_on} />
        </Meta>
        {task.blocked_on ? <Meta label={t("task.meta.blockedOn")}>{task.blocked_on}</Meta> : null}
        <Meta label={t("task.meta.created")}>{orNone(task.created.slice(0, 10))}</Meta>
        <Meta label={t("task.meta.completed")}>{orNone(task.completed.slice(0, 10))}</Meta>
      </dl>
    </header>
  );
}

function RefList({ title, refs }: { title: string; refs: TaskRef[] }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1.5 text-xs font-semibold text-muted-foreground">{title}</h3>
      {refs.length > 0 ? (
        <ul className="m-0 grid list-none gap-1.5 p-0">
          {refs.map((ref) => (
            <li key={ref.id} className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
              <Link to={ROUTES.task} params={{ taskId: ref.id }} className={`${LINK} font-mono text-xs`}>
                {ref.id}
              </Link>
              <span className="min-w-0 wrap-anywhere text-secondary-foreground">{ref.title}</span>
              <StatusChip status={statusOfFolder(ref.folder)} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-muted-foreground">{t("task.deps.none")}</p>
      )}
    </div>
  );
}

function Dependencies({ task }: { task: TaskDetail }) {
  return (
    <section className="mt-6">
      <SectionHead title={t("task.deps.title")} />
      <Card className="grid gap-4 px-4 py-3.5 shell:grid-cols-2">
        <RefList title={t("task.deps.upstream")} refs={task.depends_on} />
        <RefList title={t("task.deps.downstream")} refs={task.dependents} />
      </Card>
    </section>
  );
}

function Loading() {
  return (
    <div role="status" aria-busy="true" className="grid gap-4">
      <span className="sr-only">{t("task.loading")}</span>
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-24" />
      <Skeleton className="h-40" />
    </div>
  );
}

export function TaskPage() {
  const { taskId = "" } = useParams({ strict: false });
  const query = useQuery(taskQuery(taskId));

  let body: ReactNode;
  if (query.error instanceof ApiError && (query.error.status === HTTP_NOT_FOUND || query.error.status === HTTP_INVALID)) {
    body = <NotFound title={t("task.notFound.title")} message={t("task.notFound.message", { id: taskId })} />;
  } else if (query.isError) {
    body = (
      <Card>
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </Card>
    );
  } else if (!query.data) {
    body = <Loading />;
  } else {
    const task = query.data;
    body = (
      <>
        <TaskHeader task={task} />
        {task.body_truncated ? (
          <p role="note" className="mb-4 rounded-md border border-border-strong bg-muted px-3 py-2 text-[12.5px] text-secondary-foreground">
            {t("task.truncated.body")}
          </p>
        ) : null}
        <TaskPipeline task={task} />
        <TaskBody body={task.body} />
        <Dependencies task={task} />
      </>
    );
  }

  return (
    <>
      <BackLink />
      {body}
    </>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { statusQuery } from "@/lib/queries";
import type { DebtItem, Status } from "@/types/api";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-x-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 wrap-anywhere">{children}</dd>
    </div>
  );
}

const yesNo = (value: boolean): string => (value ? t("common.on") : t("common.off"));

function DebtList({ title, items }: { title: string; items: DebtItem[] }) {
  return (
    <div>
      <h3 className="font-semibold">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-muted-foreground">{t("harness.runtime.debt.none")}</p>
      ) : (
        <ul className="mt-1">
          {items.map((item) => (
            <li key={item.task} className="flex flex-wrap gap-x-2.5 py-0.5">
              <Link to={ROUTES.task} params={{ taskId: item.task }} className="font-mono underline underline-offset-2">
                {item.task}
              </Link>
              <span className="min-w-0 text-secondary-foreground wrap-anywhere">{item.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RuntimeBody({ status }: { status: Status }) {
  return (
    <Card className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 px-4 py-3.5">
      <div>
        <h3 className="font-semibold">{t("harness.runtime.dials")}</h3>
        {status.lanes.length === 0 ? <p className="mt-1 text-muted-foreground">{t("harness.runtime.noLanes")}</p> : null}
        <ul className="mt-1 grid grid-cols-[minmax(0,1fr)] gap-1.5">
          {status.lanes.map((lane) => (
            <li key={lane.name}>
              <dl className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
                <dt className="sr-only">{t("harness.runtime.lane", { name: lane.name })}</dt>
                <dd className="font-mono font-semibold">{lane.name}</dd>
                {lane.repo ? <Field label={t("harness.runtime.repo")}>{lane.repo}</Field> : null}
                <Field label={t("harness.runtime.mode")}>{lane.mode}</Field>
                <Field label={t("harness.runtime.approvals")}>{lane.approvals}</Field>
                <Field label={t("harness.runtime.swarm")}>{yesNo(lane.swarm)}</Field>
              </dl>
            </li>
          ))}
        </ul>
      </div>
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-1">
        <Field label={t("harness.runtime.workflow")}>
          {t("harness.runtime.workflowValue", { mode: status.workflow.mode, approval: yesNo(status.workflow.push_needs_approval) })}
        </Field>
        <Field label={t("harness.runtime.worktrees")}>
          {t("harness.runtime.worktreesValue", { enabled: yesNo(status.worktrees.enabled), max: status.worktrees.max_parallel })}
        </Field>
        <Field label={t("harness.runtime.supervisor")}>
          {t("harness.runtime.supervisorValue", { state: status.supervisor.state, runs: status.supervisor.runs })}
        </Field>
      </dl>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
        <DebtList title={t("harness.runtime.debt.handoffs", { count: status.debt.handoffs.length })} items={status.debt.handoffs} />
        <DebtList title={t("harness.runtime.debt.memory", { count: status.debt.memory.length })} items={status.debt.memory} />
      </div>
    </Card>
  );
}

/** The Runtime block at the bottom of the Plugin tab (FR-110): dials per lane, worktrees, supervisor and the debt per task. */
export function RuntimeBlock() {
  const query = useQuery(statusQuery());
  let body: ReactNode;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (query.data === undefined) {
    body = (
      <div role="status" aria-label={t("harness.runtime.loading")}>
        <Skeleton className="h-24 w-full" />
      </div>
    );
  } else {
    body = <RuntimeBody status={query.data} />;
  }
  return (
    <section className="mt-5">
      <SectionHead title={t("harness.runtime.title")} />
      {body}
    </section>
  );
}

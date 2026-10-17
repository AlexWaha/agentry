import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusChip } from "@/components/shared/StatusChip";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Folder, TaskSummary, TasksMeta } from "@/types/api";

import type { BoardSearch } from "./search";

const CARD_EDGE: Record<Folder, string> = {
  backlog: "border-l-status-backlog-edge",
  active: "border-l-status-active-edge bg-status-active-bg",
  done: "border-l-status-done-edge",
};

function BoardCard({ task }: { task: TaskSummary }) {
  return (
    <article className={cn("relative grid min-w-0 gap-1.5 rounded-md border-y border-r border-l-[3px] border-y-border border-r-border bg-card p-3", CARD_EDGE[task.folder])}>
      <div className="flex min-w-0 items-center justify-between gap-2 font-mono text-xs text-muted-foreground">
        <span className="min-w-0 truncate">{task.id}</span>
        {task.priority === "high" ? <span className="font-semibold text-foreground">{t("board.card.high")}</span> : null}
      </div>
      <Link
        to={ROUTES.task}
        params={{ taskId: task.id }}
        className="font-medium wrap-anywhere text-foreground after:absolute after:inset-0 after:content-['']"
      >
        {task.title}
      </Link>
      <ChipRow>
        {task.type ? <Chip>{task.type}</Chip> : null}
        {task.priority && task.priority !== "high" ? <Chip title={t("board.card.priority", { priority: task.priority })}>{task.priority}</Chip> : null}
        {task.epic ? <Chip>{task.epic}</Chip> : null}
        {task.assignee ? <Chip title={t("board.card.assignee", { assignee: task.assignee })}>{task.assignee}</Chip> : null}
        {task.superseded_by ? <Chip tone="dashed">{t("board.card.supersededBy", { task: task.superseded_by })}</Chip> : null}
      </ChipRow>
    </article>
  );
}

export interface BoardColumnProps {
  folder: Folder;
  query: UseQueryResult<{ data: TaskSummary[]; meta: TasksMeta }>;
  /** The board's epic filter and superseded toggle, carried into the "View all" link (FR-125). */
  search: BoardSearch;
  /** The most cards a column shows (BOARD_COLUMN_CAP). */
  cap: number;
}

/** One Board column: header with the exact total, the cards, and the capped-column footer. Handles its own four states. */
export function BoardColumn({ folder, query, search, cap }: BoardColumnProps) {
  const column = t(`chip.status.${folder}`);
  const total = query.data?.meta.total;
  const tasks = query.data?.data ?? [];

  let body: ReactNode;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (!query.data) {
    body = (
      <div role="status" aria-busy="true" className="grid gap-2 p-2.5">
        <span className="sr-only">{t("board.column.loading", { column })}</span>
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  } else if (tasks.length === 0) {
    body = <EmptyState title={t("board.column.empty")} />;
  } else {
    body = (
      <ul className="m-0 grid list-none gap-2 p-2.5">
        {tasks.map((task) => (
          <li key={task.id} className="min-w-0">
            <BoardCard task={task} />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section aria-label={column} className="min-w-0 rounded-lg border border-border bg-muted/40">
      <h2 className="m-0 flex items-center justify-between gap-2 border-b border-border px-3 py-2.5 text-[13px] font-semibold">
        <StatusChip status={folder} />
        {total !== undefined ? <span className="tabular-nums text-muted-foreground">{total}</span> : null}
      </h2>
      {body}
      {total !== undefined && total > cap ? (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border px-3 py-2.5 text-[12.5px] text-muted-foreground">
          <span>{t("board.column.showing", { shown: tasks.length, total })}</span>
          <Link
            to={ROUTES.tasks}
            search={{ folder, epic: search.epic, superseded: search.superseded }}
            className="text-foreground underline underline-offset-2"
          >
            {t("board.column.viewAll", { total })}
          </Link>
        </div>
      ) : null}
    </section>
  );
}

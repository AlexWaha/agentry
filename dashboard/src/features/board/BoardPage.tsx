import { useQueries, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { PageHeader } from "@/components/shared/PageHeader";
import { Select } from "@/components/ui/select";
import { BOARD_COLUMN_CAP } from "@/config/constants";
import { t } from "@/i18n";
import { epicsQuery, tasksQuery } from "@/lib/queries";
import type { Folder } from "@/types/api";

import { BoardColumn } from "./BoardColumn";
import type { BoardSearch } from "./search";

const FOLDERS: readonly Folder[] = ["backlog", "active", "done"];

export function BoardPage() {
  const search = useSearch({ strict: false }) as BoardSearch;
  const navigate = useNavigate();
  const setFilters = (patch: BoardSearch): void => {
    void navigate({ to: ".", search: (prev: BoardSearch) => ({ ...prev, ...patch }) });
  };
  const epics = useQuery(epicsQuery());
  const columns = useQueries({
    queries: FOLDERS.map((folder) =>
      tasksQuery({
        folder,
        limit: BOARD_COLUMN_CAP,
        epic: search.epic,
        include_superseded: search.superseded ? true : undefined,
      }),
    ),
  });
  const epicIds = (epics.data ?? []).map((epic) => epic.id);
  if (search.epic && !epicIds.includes(search.epic)) epicIds.push(search.epic);
  const epicTitles = new Map((epics.data ?? []).map((epic) => [epic.id, epic.title]));
  const hidden = columns.reduce((sum, column) => sum + (column.data?.meta.hidden_superseded ?? 0), 0);

  return (
    <>
      <PageHeader title={t("board.title")} subtitle={t("board.subtitle")} docsPage="board" />
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <Select
          aria-label={t("board.filter.epic")}
          value={search.epic ?? ""}
          onChange={(event) => setFilters({ epic: event.target.value || undefined })}
          className="max-w-full"
        >
          <option value="">{t("board.filter.allEpics")}</option>
          {epicIds.map((id) => (
            <option key={id} value={id} title={epicTitles.get(id)}>
              {id}
            </option>
          ))}
        </Select>
        {hidden > 0 || search.superseded ? (
          <div className="flex flex-wrap items-center gap-x-3 text-[12.5px] text-muted-foreground">
            <span>{t("board.superseded.hidden", { count: hidden })}</span>
            <label className="inline-flex items-center gap-1.5 text-foreground">
              <input
                type="checkbox"
                checked={search.superseded === 1}
                onChange={(event) => setFilters({ superseded: event.target.checked ? 1 : undefined })}
                className="size-4 accent-status-active-edge"
              />
              {t("board.superseded.toggle")}
            </label>
          </div>
        ) : null}
      </div>
      <div role="group" aria-label={t("board.columns")} className="grid grid-cols-[minmax(0,1fr)] items-start gap-3.5 shell:grid-cols-3">
        {FOLDERS.map((folder, index) => (
          <BoardColumn key={folder} folder={folder} query={columns[index]} search={search} cap={BOARD_COLUMN_CAP} />
        ))}
      </div>
    </>
  );
}

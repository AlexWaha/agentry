import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { t } from "@/i18n";
import { hasFilters, useSearchText, type NavigateOptions } from "@/lib/search-params";
import type { MemoryStats } from "@/types/api";

import { RETIRED_MODES, TAB_FILTERS, type FilterKey, type MemorySearch, type MemoryTab } from "./search";

export interface MemoryFiltersProps {
  tab: Exclude<MemoryTab, "stats">;
  search: MemorySearch;
  /** Agent and area options come from the stats; absent while they load. */
  stats: MemoryStats | undefined;
  setFilters: (patch: Partial<MemorySearch>, options?: NavigateOptions) => void;
  clear: () => void;
}

/** Options of a select: the known values plus the one in the URL, so a shared link never loses its filter. */
function optionsOf(counts: Record<string, number> | undefined, current: string | undefined): string[] {
  const names = new Set(Object.keys(counts ?? {}));
  if (current) names.add(current);
  return [...names].sort();
}

interface NameSelectProps {
  label: string;
  allLabel: string;
  value: string | undefined;
  counts: Record<string, number> | undefined;
  onChange: (value: string | undefined) => void;
}

function NameSelect({ label, allLabel, value, counts, onChange }: NameSelectProps) {
  return (
    <Select aria-label={label} value={value ?? ""} onChange={(event) => onChange(event.target.value || undefined)}>
      <option value="">{allLabel}</option>
      {optionsOf(counts, value).map((name) => (
        <option key={name} value={name}>
          {name}
        </option>
      ))}
    </Select>
  );
}

/** The search box and the filters that apply to the current tab (FR-103). Text boxes send after 300 ms. */
export function MemoryFilters({ tab, search, stats, setFilters, clear }: MemoryFiltersProps) {
  const allowed: readonly FilterKey[] = TAB_FILTERS[tab];
  const [text, setText] = useSearchText(search.q ?? "", (value) => setFilters({ q: value.trim() || undefined }, { replace: true }));
  const [taskText, setTaskText] = useSearchText(search.task ?? "", (value) =>
    setFilters({ task: value.trim() || undefined }, { replace: true }),
  );

  return (
    <div className="mb-3.5 flex flex-wrap items-center gap-x-3 gap-y-2.5">
      <div className="relative min-w-52 flex-1 basis-64">
        <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          className="pl-8"
          placeholder={t("memory.search.placeholder")}
          aria-label={t("memory.search.label")}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      {allowed.includes("agent") ? (
        <NameSelect
          label={t("memory.filter.agent")}
          allLabel={t("memory.filter.allAgents")}
          value={search.agent}
          counts={stats?.by_agent}
          onChange={(agent) => setFilters({ agent })}
        />
      ) : null}
      {allowed.includes("area") ? (
        <NameSelect
          label={t("memory.filter.area")}
          allLabel={t("memory.filter.allAreas")}
          value={search.area}
          counts={stats?.by_area}
          onChange={(area) => setFilters({ area })}
        />
      ) : null}
      {allowed.includes("task") ? (
        <Input
          className="w-36"
          placeholder={t("memory.filter.taskPlaceholder")}
          aria-label={t("memory.filter.task")}
          value={taskText}
          onChange={(event) => setTaskText(event.target.value)}
        />
      ) : null}
      {allowed.includes("retired") ? (
        <Select
          aria-label={t("memory.filter.retired")}
          value={search.retired ?? "exclude"}
          onChange={(event) => setFilters({ retired: RETIRED_MODES.find((mode) => mode === event.target.value) })}
        >
          <option value="exclude">{t("memory.filter.retired.exclude")}</option>
          {RETIRED_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {t(`memory.filter.retired.${mode}`)}
            </option>
          ))}
        </Select>
      ) : null}
      {hasFilters(search, allowed) ? (
        <Button variant="ghost" onClick={clear}>
          {t("memory.filter.clear")}
        </Button>
      ) : null}
    </div>
  );
}

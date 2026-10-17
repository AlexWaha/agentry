import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { Pager } from "@/components/shared/Pager";
import { TabPanel, Tabs } from "@/components/shared/Tabs";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PAGE_SIZE } from "@/config/constants";
import { t } from "@/i18n";
import { memoryQuery, memoryStatsQuery } from "@/lib/queries";
import { clampPage, hasFilters, pageOffset, useClampPage, useListSearch } from "@/lib/search-params";
import type { MemoryRow } from "@/types/api";

import { LessonCard, ModuleCard, PatternCard } from "./MemoryCards";
import { MemoryFilters } from "./MemoryFilters";
import { MemoryStatsPanel } from "./MemoryStatsPanel";
import { MEMORY_TABS, TAB_FILTERS, tabPatch, type MemorySearch, type MemoryTab } from "./search";

const ID_BASE = "memory";
const SKELETON_ROWS = 3;

function ListSkeleton() {
  return (
    <div role="status" aria-label={t("memory.loading")} className="grid gap-3">
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <Skeleton key={index} className="h-28" />
      ))}
    </div>
  );
}

function Rows({ tab, rows }: { tab: Exclude<MemoryTab, "stats">; rows: MemoryRow[] }) {
  return (
    <ul aria-label={t("memory.list.label", { noun: t(`memory.noun.${tab}`) })} className="grid min-w-0 gap-3">
      {rows.map((row) => {
        if (row.kind === "lesson") return <LessonCard key={row.id} lesson={row} />;
        if (row.kind === "pattern") return <PatternCard key={row.id} pattern={row} />;
        return <ModuleCard key={row.id} module={row} />;
      })}
    </ul>
  );
}

function MemoryList({ tab }: { tab: Exclude<MemoryTab, "stats"> }) {
  const { search, page, setPage, setFilters, clearFilters } = useListSearch<MemorySearch>();
  const stats = useQuery(memoryStatsQuery());
  const size = PAGE_SIZE.memory;
  const list = useQuery(
    memoryQuery({
      kind: tab,
      q: search.q,
      agent: search.agent,
      area: search.area,
      task: search.task,
      retired: search.retired,
      limit: size,
      offset: pageOffset(page, size),
    }),
  );
  const total = list.data?.meta.total;
  useClampPage(page, total, size, setPage);

  const filtered = hasFilters(search, TAB_FILTERS[tab]);
  const noun = t(`memory.noun.${tab}`);
  let body;
  if (list.isError) body = <Card><ErrorState error={list.error} onRetry={() => void list.refetch()} /></Card>;
  else if (!list.data) body = <ListSkeleton />;
  else if (list.data.meta.total === 0) {
    body = (
      <Card>
        {filtered ? (
          <EmptyState title={t("memory.noMatch.title")}>{t("memory.noMatch.body")}</EmptyState>
        ) : (
          <EmptyState title={t("memory.empty.title")}>{t("memory.empty.body")}</EmptyState>
        )}
      </Card>
    );
  } else {
    body = <Rows tab={tab} rows={list.data.data} />;
  }

  return (
    <>
      <MemoryFilters
        tab={tab}
        search={search}
        stats={stats.data}
        setFilters={setFilters}
        clear={() => clearFilters(TAB_FILTERS[tab])}
      />
      <h2 className="mb-2.5 text-[13px] font-semibold text-secondary-foreground">
        {search.q ? t("memory.heading.results", { query: search.q }) : t("memory.heading.latest", { noun })}
      </h2>
      {body}
      {total === undefined ? null : (
        <Pager page={clampPage(page, total, size)} size={size} total={total} noun={noun} onPage={(next) => setPage(next)} />
      )}
    </>
  );
}

function StatsTab() {
  const stats = useQuery(memoryStatsQuery());
  if (stats.isError) return <Card><ErrorState error={stats.error} onRetry={() => void stats.refetch()} /></Card>;
  if (!stats.data) return <ListSkeleton />;
  return <MemoryStatsPanel stats={stats.data} />;
}

export function MemoryPage() {
  const { search, setFilters } = useListSearch<MemorySearch>();
  const stats = useQuery(memoryStatsQuery());
  const tab = search.tab ?? "lesson";
  const tabs = MEMORY_TABS.map((value) => ({
    value,
    label: t(`memory.tab.${value}`),
    count: value === "stats" ? undefined : stats.data?.counts[value],
  }));

  return (
    <>
      <PageHeader title={t("memory.title")} subtitle={t("memory.subtitle")} docsPage="memory" />
      <Tabs
        tabs={tabs}
        value={tab}
        label={t("memory.tabs.label")}
        idBase={ID_BASE}
        className="mb-4"
        onChange={(next) => setFilters(tabPatch(next))}
      />
      <TabPanel idBase={ID_BASE} value={tab}>
        {tab === "stats" ? <StatsTab /> : <MemoryList tab={tab} />}
      </TabPanel>
    </>
  );
}

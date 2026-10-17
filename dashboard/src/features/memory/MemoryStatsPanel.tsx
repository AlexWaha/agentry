import { Link } from "@tanstack/react-router";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHead } from "@/components/shared/PageHeader";
import { StatCard } from "@/components/shared/StatCard";
import { Card } from "@/components/ui/card";
import { taskPath } from "@/config/routes";
import { t } from "@/i18n";
import { bytes, stamp } from "@/lib/format";
import type { MemoryStats } from "@/types/api";

import { GrowthChart } from "./GrowthChart";

function CountList({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  return (
    <section className="min-w-0">
      <SectionHead title={title} />
      <Card className="px-4 py-3">
        {entries.length === 0 ? (
          <p className="text-muted-foreground">{t("memory.stats.noneCounted")}</p>
        ) : (
          <ul className="grid gap-1.5">
            {entries.map(([name, count]) => (
              <li key={name} className="flex min-w-0 items-baseline justify-between gap-3">
                <span className="min-w-0 font-mono text-[12.5px] wrap-anywhere">{name}</span>
                <span className="tabular-nums">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

function Injection({ injection }: { injection: MemoryStats["injection"] }) {
  return (
    <section>
      <SectionHead title={t("memory.stats.injection")} />
      <p className="mb-2.5 text-muted-foreground">{t("memory.stats.injectionNote")}</p>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1.5">
        <span>
          {t("memory.stats.budget")}: <b className="tabular-nums">{bytes(injection.budget_bytes)}</b>
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
          {t("memory.stats.activeTasks")}:
          {injection.active_tasks.length === 0 ? (
            <span className="text-muted-foreground">{t("memory.stats.noActiveTasks")}</span>
          ) : (
            <ChipRow>
              {injection.active_tasks.map((id) => (
                <Chip key={id} wrap>
                  <Link to={taskPath(id)}>{id}</Link>
                </Chip>
              ))}
            </ChipRow>
          )}
        </span>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-120 border-collapse text-left">
          <caption className="sr-only">{t("memory.stats.profiles")}</caption>
          <thead>
            <tr className="border-b border-border text-xs text-muted-foreground">
              <th scope="col" className="px-4 py-2 font-medium">{t("memory.stats.col.profile")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("memory.stats.col.kinds")}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{t("memory.stats.col.limit")}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{t("memory.stats.col.matched")}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{t("memory.stats.col.shown")}</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">{t("memory.stats.col.bytes")}</th>
            </tr>
          </thead>
          <tbody>
            {injection.profiles.map((profile) => (
              <tr key={profile.matcher} className="border-b border-border last:border-0 align-top">
                <th scope="row" className="max-w-72 px-4 py-2 text-left font-mono text-[12px] font-normal wrap-anywhere">
                  {profile.matcher}
                </th>
                <td className="px-3 py-2 font-mono text-[12px] wrap-anywhere">{profile.kinds.join(", ")}</td>
                <td className="px-3 py-2 text-right tabular-nums">{profile.limit}</td>
                <td className="px-3 py-2 text-right tabular-nums">{profile.matched}</td>
                <td className="px-3 py-2 text-right tabular-nums">{profile.shown}</td>
                <td className="px-4 py-2 text-right tabular-nums">{profile.bytes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

/** The Stats tab (FR-105): file sizes, counts, growth, counts by agent and area, stamps and the injection block. */
export function MemoryStatsPanel({ stats }: { stats: MemoryStats }) {
  const rows = stats.counts.lesson + stats.counts.pattern + stats.counts.module;
  if (rows === 0 && stats.db_bytes === 0) {
    return (
      <Card>
        <EmptyState title={t("memory.stats.empty.title")}>{t("memory.stats.empty.body")}</EmptyState>
      </Card>
    );
  }
  return (
    <div className="grid min-w-0 gap-6">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(10rem,1fr))] gap-3">
        <StatCard label={t("memory.stats.db")} value={bytes(stats.db_bytes)} />
        <StatCard label={t("memory.stats.wal")} value={bytes(stats.wal_bytes)} />
        <StatCard label={t("memory.stats.lessons")} value={stats.counts.lesson} />
        <StatCard label={t("memory.stats.patterns")} value={stats.counts.pattern} />
        <StatCard label={t("memory.stats.modules")} value={stats.counts.module} />
      </div>
      {stats.growth.length > 0 ? (
        <section>
          <SectionHead title={t("memory.stats.growth")} right={t("memory.stats.growthRange", { days: stats.growth.length })} />
          <GrowthChart growth={stats.growth} />
        </section>
      ) : null}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(15rem,1fr))] gap-6">
        <CountList title={t("memory.stats.byAgent")} counts={stats.by_agent} />
        <CountList title={t("memory.stats.byArea")} counts={stats.by_area} />
      </div>
      <section>
        <SectionHead title={t("memory.stats.stamps")} />
        <Card className="px-4 py-3">
          {stats.stamps.length === 0 ? (
            <p className="text-muted-foreground">{t("memory.stats.noStamps")}</p>
          ) : (
            <ul className="grid gap-1.5">
              {stats.stamps.map((entry) => (
                <li key={entry.task} className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <Link to={taskPath(entry.task)} className="font-mono text-[12.5px] hover:underline">
                    {entry.task}
                  </Link>
                  <time dateTime={entry.at} className="tabular-nums text-muted-foreground">
                    {stamp(entry.at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>
      <Injection injection={stats.injection} />
    </div>
  );
}

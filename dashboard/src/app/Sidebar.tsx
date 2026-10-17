import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { BookOpen, ExternalLink } from "lucide-react";

import { ThemeSwitch } from "@/app/ThemeSwitch";
import { Logo } from "@/components/shared/Logo";
import { Skeleton } from "@/components/ui/skeleton";
import { DOCS_URL } from "@/config/constants";
import { NAV_ENTRIES, type NavEntry } from "@/config/nav";
import { t } from "@/i18n";
import { runsQuery, statusQuery } from "@/lib/queries";
import { cn } from "@/lib/utils";
import type { Lane, Status } from "@/types/api";

const NAV_LINK =
  "flex items-center gap-2.5 rounded-md px-2 py-[7px] text-[13.5px] whitespace-nowrap text-secondary-foreground hover:bg-accent hover:text-accent-foreground aria-[current=page]:bg-accent aria-[current=page]:text-accent-foreground";

function NavItem({ entry, needsCount }: { entry: NavEntry; needsCount: number }) {
  const Icon = entry.icon;
  const showCount = entry.id === "needs" && needsCount > 0;
  return (
    <Link to={entry.to} activeOptions={{ exact: entry.id === "overview" }} activeProps={{ "aria-current": "page" }} className={NAV_LINK}>
      <Icon aria-hidden="true" className="size-4 shrink-0 opacity-85" strokeWidth={1.6} />
      {t(entry.labelKey)}
      {showCount ? (
        <span
          aria-label={t("shell.nav.needsCount", { count: needsCount })}
          className="ml-auto inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-attention px-1.5 text-[11px] font-semibold text-on-attention"
        >
          {needsCount}
        </span>
      ) : null}
    </Link>
  );
}

function defaultLane(status: Status): Lane | undefined {
  return status.lanes.find((lane) => lane.name === "default") ?? status.lanes[0];
}

function Dials({ status }: { status: Status | undefined }) {
  if (!status) {
    return (
      <div className="grid gap-1.5" aria-hidden="true">
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-full" />
      </div>
    );
  }
  const lane = defaultLane(status);
  const rows: [string, string][] = [
    [t("shell.dials.workflow"), status.workflow.mode],
    ...(lane
      ? ([
          [t("shell.dials.mode"), lane.mode],
          [t("shell.dials.approvals"), lane.approvals],
          [t("shell.dials.swarm"), lane.swarm ? t("common.on") : t("common.off")],
        ] as [string, string][])
      : []),
  ];
  return (
    <dl className="grid gap-1 text-[12.5px]">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-mono text-xs text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The left sidebar (FR-76, FR-129): logo and name, the ten nav entries with the "Needs you" count, the Docs
 * link, the theme switch, the dials, the Read-only tag and the plugin version. Below the `shell` breakpoint
 * it becomes a top block whose nav row scrolls sideways; the Docs link and theme switch stay in the first row,
 * so nothing needs a menu at 768px.
 */
export function Sidebar() {
  const status = useQuery(statusQuery());
  const runs = useQuery(runsQuery());
  const needsCount = runs.data?.filter((run) => run.needs_you !== null).length ?? 0;
  const project = status.data?.project;

  return (
    <aside
      aria-label={t("shell.sidebar")}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-sidebar px-3 py-2.5 shell:sticky shell:top-0 shell:h-screen shell:flex-col shell:flex-nowrap shell:items-stretch shell:gap-y-1.5 shell:border-r shell:border-b-0 shell:px-3 shell:pt-4.5 shell:pb-3.5"
    >
      <div className="order-1 flex items-center gap-2.5 px-2 shell:order-none">
        <Logo size={26} />
        <span className="text-[17px] font-extrabold tracking-tight">{t("shell.brand")}</span>
      </div>
      {project ? (
        <p title={project} className="hidden truncate px-2 pb-3.5 font-mono text-xs text-muted-foreground shell:order-none shell:block">
          {project}
        </p>
      ) : null}
      <nav
        aria-label={t("shell.nav.label")}
        className="order-4 flex w-full gap-0.5 overflow-x-auto shell:order-none shell:grid shell:overflow-visible"
      >
        {NAV_ENTRIES.map((entry) => (
          <NavItem key={entry.id} entry={entry} needsCount={needsCount} />
        ))}
      </nav>
      <a
        href={DOCS_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(NAV_LINK, "order-2 ml-auto shell:order-none shell:mt-2.5 shell:ml-0 shell:border-t shell:border-border shell:pt-3")}
      >
        <BookOpen aria-hidden="true" className="size-4 shrink-0 opacity-85" strokeWidth={1.6} />
        {t("shell.docs")}
        <ExternalLink aria-hidden="true" className="size-3 opacity-60 shell:ml-auto" />
        <span className="sr-only">{t("shell.docs.newTab")}</span>
      </a>
      <div className="order-3 flex flex-wrap items-center gap-x-3 gap-y-2 shell:order-none shell:mt-auto shell:grid shell:gap-3 shell:border-t shell:border-border shell:px-2 shell:pt-3">
        <ThemeSwitch />
        <div className="hidden shell:block">
          <Dials status={status.data} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border-strong px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wide text-muted-foreground">
            {t("shell.readOnly")}
          </span>
          {status.data ? (
            <span className="font-mono text-xs text-muted-foreground">{t("shell.version", { version: status.data.plugin_version })}</span>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

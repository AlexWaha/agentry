import { Outlet, useRouterState } from "@tanstack/react-router";

import { ConnectionIndicator } from "@/app/ConnectionIndicator";
import { ExpiredLinkPage } from "@/app/ExpiredLinkPage";
import { Sidebar } from "@/app/Sidebar";
import { NAV_ENTRIES } from "@/config/nav";
import { t } from "@/i18n";
import { useTokenExpired } from "@/lib/token";

/** A path segment as text; a malformed percent escape stays as typed instead of throwing. */
function readable(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/** The crumb text: the nav label of the current section, or the id for a detail page. */
function crumbOf(pathname: string): string {
  const [section, id] = pathname.split("/").filter(Boolean);
  if (!section) return t("shell.nav.overview");
  if (id) return readable(id);
  const entry = NAV_ENTRIES.find((item) => item.to === `/${section}`);
  return entry ? t(entry.labelKey) : t("shell.nav.notFound");
}

/** The frame of every page: sidebar, top bar with the connection state, and the page outlet (FR-76). */
export function Shell() {
  const expired = useTokenExpired();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <div className="min-h-screen shell:grid shell:grid-cols-[var(--raw-sidebar-width)_minmax(0,1fr)]">
      <a
        href="#view"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-20 focus:rounded-md focus:border focus:border-border-strong focus:bg-card focus:px-2.5 focus:py-1.5"
      >
        {t("shell.skip")}
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-col">
        <header className="z-10 flex h-12 items-center justify-between gap-4 border-b border-border bg-background/90 px-4 text-[13px] backdrop-blur-sm shell:sticky shell:top-0 shell:px-7">
          <nav aria-label={t("shell.crumbs")} className="flex min-w-0 items-center gap-2 text-muted-foreground">
            <span>{t("shell.brand")}</span>
            <span aria-hidden="true" className="text-border-strong">
              /
            </span>
            <span aria-current="page" className="truncate text-foreground">
              {crumbOf(pathname)}
            </span>
          </nav>
          <ConnectionIndicator />
        </header>
        <main id="view" tabIndex={-1} className="w-full max-w-(--raw-content-max) px-4 pt-5 pb-10 outline-none shell:px-7 shell:pt-7 shell:pb-14">
          {expired ? <ExpiredLinkPage /> : <Outlet />}
        </main>
      </div>
    </div>
  );
}

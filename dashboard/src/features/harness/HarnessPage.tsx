import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { PageHeader } from "@/components/shared/PageHeader";
import { TabPanel, Tabs } from "@/components/shared/Tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n";
import { harnessQuery } from "@/lib/queries";
import type { Harness } from "@/types/api";

import { AgentsPanel } from "./AgentsPanel";
import { HooksPanel } from "./HooksPanel";
import { PluginPanel } from "./PluginPanel";
import { RulesPanel } from "./RulesPanel";
import { DEFAULT_TAB, HARNESS_TABS, tabOf, type HarnessSearch, type HarnessTab } from "./search";
import { SkillsPanel } from "./SkillsPanel";

const TABS_ID = "harness";

function panelOf(tab: HarnessTab, harness: Harness): ReactNode {
  switch (tab) {
    case "agents":
      return <AgentsPanel agents={harness.agents} />;
    case "rules":
      return <RulesPanel rules={harness.rules} />;
    case "skills":
      return <SkillsPanel skills={harness.skills} />;
    case "hooks":
      return <HooksPanel hooks={harness.hooks} />;
    case "plugin":
      return <PluginPanel plugin={harness.plugin} />;
  }
}

const countOf = (tab: HarnessTab, harness: Harness): number | undefined =>
  tab === "plugin" ? undefined : harness[tab].length;

function Loading() {
  return (
    <div role="status" aria-label={t("harness.loading")} className="grid gap-3">
      <Skeleton className="h-8 w-80 max-w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

/** Harness (FR-109, FR-127): the agents, rules, skills, hooks and plugin as five tabs, the tab held in the URL. */
export function HarnessPage() {
  const query = useQuery(harnessQuery());
  const search = useSearch({ strict: false }) as HarnessSearch;
  const navigate = useNavigate();
  const tab = tabOf(search.tab);

  const select = (value: HarnessTab): void => {
    void navigate({ to: ".", search: { tab: value === DEFAULT_TAB ? undefined : value } });
  };

  let body: ReactNode;
  if (query.isError) {
    body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else if (query.data === undefined) {
    body = <Loading />;
  } else {
    const harness = query.data;
    body = (
      <>
        <Tabs<HarnessTab>
          idBase={TABS_ID}
          label={t("harness.tabs.label")}
          value={tab}
          onChange={select}
          tabs={HARNESS_TABS.map((value) => ({ value, label: t(`harness.tab.${value}`), count: countOf(value, harness) }))}
        />
        <TabPanel idBase={TABS_ID} value={tab} className="mt-4 outline-offset-4">
          {panelOf(tab, harness)}
        </TabPanel>
      </>
    );
  }

  return (
    <>
      <PageHeader title={t("harness.title")} subtitle={t("harness.subtitle")} docsPage="harness" />
      {body}
    </>
  );
}

import { PageHeader } from "@/components/shared/PageHeader";
import { TabPanel, Tabs } from "@/components/shared/Tabs";
import { t } from "@/i18n";
import { useListSearch } from "@/lib/search-params";

import { CodeGraphView } from "./CodeGraphView";
import { MemoryGraphView } from "./MemoryGraphView";
import { GRAPH_VIEWS, type GraphsSearch } from "./search";

const ID_BASE = "graphs";

export function GraphsPage() {
  const { search, setFilters } = useListSearch<GraphsSearch>();
  const view = search.view ?? "memory";
  return (
    <>
      <PageHeader title={t("graphs.title")} subtitle={t("graphs.subtitle")} docsPage="graphs" />
      <Tabs
        tabs={GRAPH_VIEWS.map((value) => ({ value, label: t(`graphs.view.${value}`) }))}
        value={view}
        label={t("graphs.views.label")}
        idBase={ID_BASE}
        className="mb-4"
        onChange={(next) => setFilters({ view: next === "memory" ? undefined : next, index: undefined })}
      />
      <TabPanel idBase={ID_BASE} value={view}>
        {view === "memory" ? (
          <MemoryGraphView />
        ) : (
          <CodeGraphView indexKey={search.index} onSelect={(index) => setFilters({ index })} />
        )}
      </TabPanel>
    </>
  );
}

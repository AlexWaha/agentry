import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { Harness } from "@/types/api";

import { RuntimeBlock } from "./RuntimeBlock";

export function PluginPanel({ plugin }: { plugin: Harness["plugin"] }) {
  const rows: [string, string][] = [
    [t("harness.plugin.name"), plugin.name],
    [t("harness.plugin.version"), plugin.version],
    [t("harness.plugin.source"), t(`harness.plugin.install.${plugin.install}`)],
    [t("harness.plugin.root"), plugin.root],
  ];
  return (
    <section>
      <SectionHead title={t("harness.plugin.title")} />
      <Card className="min-w-0 px-4 py-3">
        <dl className="grid grid-cols-[minmax(0,1fr)] gap-x-4 gap-y-1.5 sm:grid-cols-[max-content_minmax(0,1fr)]">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 font-mono wrap-anywhere">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>
      <RuntimeBlock />
    </section>
  );
}

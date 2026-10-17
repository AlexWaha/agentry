import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { bytes } from "@/lib/format";
import type { HarnessRule } from "@/types/api";

const DELIVERIES = ["core", "main-thread", "targeted", "unused"] as const;

const sum = (rules: HarnessRule[]): number => rules.reduce((total, rule) => total + rule.bytes, 0);

export function RulesPanel({ rules }: { rules: HarnessRule[] }) {
  if (rules.length === 0) {
    return <EmptyState title={t("harness.rules.empty")}>{t("harness.rules.emptyHint")}</EmptyState>;
  }
  return (
    <section>
      <SectionHead title={t("harness.rules.title")} right={t("harness.rules.total", { count: rules.length, size: bytes(sum(rules)) })} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3.5">
        {DELIVERIES.map((delivery) => {
          const group = rules.filter((rule) => rule.delivery === delivery).sort((a, b) => b.bytes - a.bytes);
          if (group.length === 0) return null;
          return (
            <Card key={delivery} className="min-w-0">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-border px-4 py-2.5">
                <h3 className="font-semibold">{t(`harness.rules.${delivery}`)}</h3>
                <span className="text-muted-foreground">{t(`harness.rules.${delivery}.hint`)}</span>
                <span className="ml-auto text-muted-foreground tabular-nums">
                  {t("harness.rules.groupTotal", { count: group.length, size: bytes(sum(group)) })}
                </span>
              </div>
              <ul>
                {group.map((rule) => (
                  <li key={rule.name} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border px-4 py-2 first:border-t-0">
                    <span className="min-w-0 font-mono wrap-anywhere">{rule.name}</span>
                    <span className="text-muted-foreground tabular-nums">{bytes(rule.bytes)}</span>
                    {delivery === "targeted" && rule.agents.length > 0 ? (
                      <ChipRow className="basis-full">
                        <span className="text-muted-foreground">{t("harness.rules.usedBy")}</span>
                        {rule.agents.map((agent) => (
                          <Chip key={agent} wrap>
                            {agent}
                          </Chip>
                        ))}
                      </ChipRow>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

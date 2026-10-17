import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { HarnessSkill } from "@/types/api";

export function SkillsPanel({ skills }: { skills: HarnessSkill[] }) {
  if (skills.length === 0) {
    return <EmptyState title={t("harness.skills.empty")}>{t("harness.skills.emptyHint")}</EmptyState>;
  }
  return (
    <section>
      <SectionHead title={t("harness.skills.title")} right={t("harness.skills.total", { count: skills.length })} />
      <Card className="min-w-0">
        <ul>
          {skills.map((skill) => (
            <li key={`${skill.source}:${skill.name}`} className="border-t border-border px-4 py-3 first:border-t-0">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <b className="min-w-0 font-mono font-semibold wrap-anywhere">{skill.command || skill.name}</b>
                <Chip title={t("harness.skills.source", { source: skill.source })}>{skill.source}</Chip>
                {skill.user_only ? (
                  <Chip tone="dashed" title={t("harness.skills.userOnly.title")}>
                    {t("harness.skills.userOnly")}
                  </Chip>
                ) : null}
              </div>
              <p className="mt-1 text-secondary-foreground wrap-anywhere">{skill.description || t("harness.skills.noDescription")}</p>
              {skill.preloaded_by.length > 0 ? (
                <ChipRow className="mt-1.5">
                  <span className="text-muted-foreground">{t("harness.skills.preloadedBy")}</span>
                  {skill.preloaded_by.map((agent) => (
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
    </section>
  );
}

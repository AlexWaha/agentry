import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { HarnessAgent } from "@/types/api";

interface Department {
  name: string;
  agents: HarnessAgent[];
}

/** Agents grouped by department in the order the roster lists them; agents without a department come last. */
function byDepartment(agents: HarnessAgent[]): Department[] {
  const groups = new Map<string, HarnessAgent[]>();
  for (const agent of agents) {
    const key = agent.department || t("harness.agents.other");
    groups.set(key, [...(groups.get(key) ?? []), agent]);
  }
  const other = t("harness.agents.other");
  const named = [...groups].filter(([name]) => name !== other);
  const unnamed = [...groups].filter(([name]) => name === other);
  return [...named, ...unnamed].map(([name, list]) => ({ name, agents: list }));
}

function AgentRow({ agent }: { agent: HarnessAgent }) {
  return (
    <details className="border-t border-border px-4 py-2.5 first:border-t-0">
      <summary className="flex cursor-pointer flex-wrap items-center gap-x-2.5 gap-y-1">
        <b className="min-w-0 font-semibold wrap-anywhere">{agent.name}</b>
        {agent.profile ? <Chip title={t("harness.agents.profile", { profile: agent.profile })}>{agent.profile}</Chip> : null}
        <span className="min-w-0 text-muted-foreground wrap-anywhere">{agent.model}</span>
        <span className="text-muted-foreground">
          {t("harness.agents.summary", { skills: agent.skills.length, rules: agent.rules.length })}
        </span>
      </summary>
      <p className="mt-2 text-secondary-foreground wrap-anywhere">{agent.description || t("harness.agents.noDescription")}</p>
      {agent.rules.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-baseline gap-2">
          <span className="text-muted-foreground">{t("harness.agents.rules")}</span>
          <ChipRow>
            {agent.rules.map((rule) => (
              <Chip key={rule} tone="dashed" wrap>
                {rule}
              </Chip>
            ))}
          </ChipRow>
        </div>
      ) : null}
      {agent.skills.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-baseline gap-2">
          <span className="text-muted-foreground">{t("harness.agents.skills")}</span>
          <ChipRow>
            {agent.skills.map((skill) => (
              <Chip key={skill} wrap>
                {skill}
              </Chip>
            ))}
          </ChipRow>
        </div>
      ) : null}
    </details>
  );
}

export function AgentsPanel({ agents }: { agents: HarnessAgent[] }) {
  if (agents.length === 0) {
    return <EmptyState title={t("harness.agents.empty")}>{t("harness.agents.emptyHint")}</EmptyState>;
  }
  return (
    <section>
      <SectionHead title={t("harness.agents.title")} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3.5 md:grid-cols-2">
        {byDepartment(agents).map((department) => (
          <Card key={department.name} className="min-w-0">
            <div className="flex items-baseline justify-between border-b border-border px-4 py-2.5">
              <h3 className="min-w-0 font-semibold wrap-anywhere">{department.name}</h3>
              <span className="text-muted-foreground tabular-nums">{department.agents.length}</span>
            </div>
            {department.agents.map((agent) => (
              <AgentRow key={agent.name} agent={agent} />
            ))}
          </Card>
        ))}
      </div>
    </section>
  );
}

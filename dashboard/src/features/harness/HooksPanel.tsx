import { TriangleAlert } from "lucide-react";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHead } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { HarnessHook } from "@/types/api";

import { splitMatcher } from "./search";

/** Hooks grouped by event, events in the order of their first hook. */
function byEvent(hooks: HarnessHook[]): [string, HarnessHook[]][] {
  const groups = new Map<string, HarnessHook[]>();
  for (const hook of hooks) groups.set(hook.event, [...(groups.get(hook.event) ?? []), hook]);
  return [...groups];
}

function HookRow({ hook }: { hook: HarnessHook }) {
  const matchers = splitMatcher(hook.matcher);
  return (
    <li className="border-t border-border px-4 py-2.5 first:border-t-0">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <span className="min-w-0 font-mono wrap-anywhere">{hook.script || hook.command}</span>
        {!hook.script_exists ? (
          <Chip wrap tone="dashed" title={t("harness.hooks.missing.title")} className="inline-flex items-center gap-1 font-semibold text-foreground">
            <TriangleAlert aria-hidden="true" className="size-3 shrink-0" />
            {t("harness.hooks.missing")}
          </Chip>
        ) : null}
        <Chip title={t("harness.hooks.source", { source: hook.source })}>{hook.source}</Chip>
        {hook.timeout === null ? null : <span className="text-muted-foreground tabular-nums">{t("harness.hooks.timeout", { seconds: hook.timeout })}</span>}
      </div>
      <ChipRow className="mt-1.5">
        <span className="text-muted-foreground">{t("harness.hooks.matcher")}</span>
        {matchers.length === 0 ? (
          <span className="text-muted-foreground">{t("harness.hooks.allTools")}</span>
        ) : (
          matchers.map((matcher, index) => (
            <Chip key={`${index}:${matcher}`} wrap tone="dashed">
              {matcher}
            </Chip>
          ))
        )}
      </ChipRow>
    </li>
  );
}

export function HooksPanel({ hooks }: { hooks: HarnessHook[] }) {
  if (hooks.length === 0) {
    return <EmptyState title={t("harness.hooks.empty")}>{t("harness.hooks.emptyHint")}</EmptyState>;
  }
  const events = byEvent(hooks);
  return (
    <section>
      <SectionHead title={t("harness.hooks.title")} right={t("harness.hooks.total", { count: hooks.length, events: events.length })} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3.5">
        {events.map(([event, list]) => (
          <Card key={event} className="min-w-0">
            <div className="flex items-baseline justify-between border-b border-border px-4 py-2.5">
              <h3 className="font-semibold">{event}</h3>
              <span className="text-muted-foreground tabular-nums">{list.length}</span>
            </div>
            <ul>
              {list.map((hook, index) => (
                <HookRow key={`${hook.source}:${hook.command}:${index}`} hook={hook} />
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </section>
  );
}

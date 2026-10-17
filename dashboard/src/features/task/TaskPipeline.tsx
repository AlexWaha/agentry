import { EmptyState } from "@/components/shared/EmptyState";
import { EventTimeline } from "@/components/shared/EventTimeline";
import { SectionHead } from "@/components/shared/PageHeader";
import { Stepper } from "@/components/shared/Stepper";
import { Card } from "@/components/ui/card";
import { t } from "@/i18n";
import type { TaskDetail } from "@/types/api";

/** The Pipeline block (FR-98): the stepper when the task has a run row, then its event timeline. */
export function TaskPipeline({ task }: { task: TaskDetail }) {
  return (
    <section className="mt-6">
      <SectionHead
        title={t("task.pipeline.title")}
        right={t("task.pipeline.summary", { gates: task.gate_runs, failed: task.gate_failures, rejections: task.rejections })}
      />
      <Card className="grid gap-4 px-4 py-3.5">
        {task.run ? <Stepper stages={task.stages} blocked={task.run.stage_status === "blocked"} needsYou={task.run.needs_you !== null} /> : null}
        <div>
          <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t("task.pipeline.history")}</h3>
          {task.events.length > 0 ? (
            <EventTimeline events={task.events} />
          ) : (
            <EmptyState title={t("task.pipeline.noEvents")} className="px-0 py-2" />
          )}
          {task.events_truncated ? (
            <p role="note" className="mt-2 text-xs text-muted-foreground">
              {t("task.pipeline.truncated")}
            </p>
          ) : null}
        </div>
      </Card>
    </section>
  );
}

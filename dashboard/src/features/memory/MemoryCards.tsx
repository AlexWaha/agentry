import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Chip, ChipRow } from "@/components/shared/Chip";
import { Card } from "@/components/ui/card";
import { taskPath } from "@/config/routes";
import { t } from "@/i18n";
import type { Lesson, Module, Pattern } from "@/types/api";

const TASK_ID = /^task-\d{4,}$/;
const DATE_LENGTH = 10;

const day = (iso: string): string => iso.slice(0, DATE_LENGTH);

/** A task id links to its page; any other source text is a plain chip. Never ellipsized (FR-128). */
function TaskLink({ id }: { id: string }) {
  if (!id) return null;
  if (!TASK_ID.test(id)) return <Chip wrap>{id}</Chip>;
  return (
    <Chip wrap>
      <Link to={taskPath(id)} className="underline-offset-2 hover:underline">
        {id}
      </Link>
    </Chip>
  );
}

function Fields({ children }: { children: ReactNode }) {
  return <dl className="mt-2.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5">{children}</dl>;
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  if (!value) return null;
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-[12.5px] wrap-anywhere" : "wrap-anywhere"}>{value}</dd>
    </>
  );
}

function CardHead({ title, mono = false, children }: { title: string; mono?: boolean; children?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
      <h3 className={mono ? "min-w-0 font-mono text-[13px] font-semibold wrap-anywhere" : "min-w-0 font-semibold wrap-anywhere"}>{title}</h3>
      {children ? <ChipRow className="justify-end">{children}</ChipRow> : null}
    </div>
  );
}

export function LessonCard({ lesson }: { lesson: Lesson }) {
  return (
    <li className="min-w-0">
      <Card className="min-w-0 px-4 py-3.5">
        <CardHead title={lesson.signature}>
          {lesson.retired ? <Chip tone="dashed">{t("memory.lesson.retired")}</Chip> : null}
          {lesson.area ? (
            <Chip wrap title={t("memory.lesson.area")}>
              {lesson.area}
            </Chip>
          ) : null}
          <TaskLink id={lesson.task} />
          {lesson.agent ? (
            <Chip wrap title={t("memory.lesson.agent")}>
              {lesson.agent}
            </Chip>
          ) : null}
        </CardHead>
        <Fields>
          <Field label={t("memory.lesson.trigger")} value={lesson.trigger} />
          <Field label={t("memory.lesson.what")} value={lesson.what} />
          <Field label={t("memory.lesson.why")} value={lesson.why} />
          <Field label={t("memory.lesson.fix")} value={lesson.fix} />
        </Fields>
        <p className="mt-2.5 text-xs text-muted-foreground">{t("memory.lesson.created", { date: day(lesson.created) })}</p>
      </Card>
    </li>
  );
}

export function PatternCard({ pattern }: { pattern: Pattern }) {
  return (
    <li className="min-w-0">
      <Card className="min-w-0 px-4 py-3.5">
        <CardHead title={pattern.name}>
          <TaskLink id={pattern.task} />
        </CardHead>
        <Fields>
          <Field label={t("memory.pattern.useWhen")} value={pattern.use_when} />
        </Fields>
        {pattern.body ? (
          <pre
            aria-label={t("memory.pattern.body")}
            className="mt-2.5 max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap wrap-anywhere"
          >
            {pattern.body}
          </pre>
        ) : null}
        <p className="mt-2.5 text-xs text-muted-foreground">{t("memory.pattern.created", { date: day(pattern.created) })}</p>
      </Card>
    </li>
  );
}

export function ModuleCard({ module }: { module: Module }) {
  return (
    <li className="min-w-0">
      <Card className="min-w-0 px-4 py-3.5">
        <CardHead title={module.path} mono />
        <Fields>
          <Field label={t("memory.module.responsibility")} value={module.responsibility} />
          <Field label={t("memory.module.symbols")} value={module.symbols} mono />
          <Field label={t("memory.module.notes")} value={module.notes} />
        </Fields>
        <p className="mt-2.5 text-xs text-muted-foreground">{t("memory.module.updated", { date: day(module.updated) })}</p>
      </Card>
    </li>
  );
}

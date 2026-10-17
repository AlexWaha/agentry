import { Check, User } from "lucide-react";

import { STAGE_LABEL_KEYS } from "@/config/stages";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Stage } from "@/types/api";

export interface StepperProps {
  /** `Run.stages` or `TaskDetail.stages`, computed by the server (FR-20). */
  stages: Stage[];
  /** True when the run is blocked: the current step then reads "Blocked" and takes the attention color. */
  blocked?: boolean;
  /** True only when the run waits for the User (`run.needs_you` is not null): the current step then takes the attention color. */
  needsYou?: boolean;
  className?: string;
}

const FINAL_STAGE = "done";

export function stageLabel(name: string): string {
  const key = STAGE_LABEL_KEYS[name];
  return key ? t(key) : name;
}

function noteOf(stage: Stage, blocked: boolean, needsYou: boolean): string {
  if (stage.state === "skipped") return t("shell.stepper.skipped");
  if (stage.state === "now") return blocked ? t("shell.stepper.blocked") : stage.human && needsYou ? t("shell.stepper.waiting") : t("shell.stepper.inProgress");
  if (stage.state === "done" && stage.human) return t("shell.stepper.approved");
  if (stage.state === "done" && stage.name === FINAL_STAGE) return t("shell.stepper.merged");
  if (stage.pass === 0 && stage.fail === 0) return "";
  return stage.fail > 0 ? t("shell.stepper.gatesWithFailures", { pass: stage.pass, fail: stage.fail }) : t("shell.stepper.gates", { pass: stage.pass });
}

/**
 * The pipeline of a run as steps (FR-98): finished steps are checked, the current one is ringed, human
 * checkpoints show a person, skipped steps are dashed, and gate pass/fail counts sit under the label. Orange
 * is for a run that waits for the User only (`needsYou`), never for a checkpoint nobody is waiting on. Every
 * step carries a text note, so state is never conveyed by color alone. The wrapper pads the scroll area so the
 * 4px ring of the current step is not clipped.
 */
export function Stepper({ stages, blocked = false, needsYou: waiting = false, className }: StepperProps) {
  return (
    <div className={cn("-mx-2 -mt-2 -mb-1.5 overflow-x-auto px-2 pt-2 pb-1.5", className)}>
      <ol className="grid min-w-140 auto-cols-[minmax(78px,1fr)] grid-flow-col">
        {stages.map((stage, index) => {
          const now = stage.state === "now";
          const done = stage.state === "done";
          const needsYou = now && waiting;
          const note = noteOf(stage, blocked, waiting);
          return (
            <li
              key={stage.name}
              data-state={stage.state}
              aria-current={now ? "step" : undefined}
              className={cn(
                "relative grid justify-items-center gap-2 text-center text-xs text-muted-foreground",
                index > 0 && "before:absolute before:top-2.5 before:right-1/2 before:w-full before:border-t before:border-border-strong",
                (done || now) && index > 0 && "before:border-status-done-edge",
                done && "text-secondary-foreground",
                now && "font-semibold text-foreground",
              )}
            >
              <span
                className={cn(
                  "relative z-10 grid size-5 place-items-center rounded-full border-[1.5px] border-border-strong bg-card",
                  done && "border-status-done-edge bg-status-done-edge text-on-status",
                  now && !needsYou && "border-status-active-edge text-status-active-edge ring-4 ring-status-active-edge/20",
                  needsYou && "border-attention text-attention ring-4 ring-attention-bg",
                  stage.state === "skipped" && "border-dashed",
                )}
              >
                {done ? <Check aria-hidden="true" className="size-3" strokeWidth={2.4} /> : null}
                {!done && stage.human ? <User aria-hidden="true" className="size-3" strokeWidth={2.2} /> : null}
                {now && !stage.human ? <span aria-hidden="true" className="size-[7px] rounded-full bg-current" /> : null}
              </span>
              <span>{stageLabel(stage.name)}</span>
              <small
                className={cn(
                  "-mt-1 block min-h-[15px] text-[11px] font-normal text-muted-foreground",
                  now && !needsYou && "text-status-active-fg",
                  needsYou && "text-attention",
                )}
              >
                {note}
              </small>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

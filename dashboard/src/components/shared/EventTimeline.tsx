import { Check, Circle, Undo2, User, X, type LucideIcon } from "lucide-react";

import { EVENT_KIND_KEYS } from "@/config/stages";
import { t } from "@/i18n";
import { ms, stamp } from "@/lib/format";
import { stageLabel } from "@/components/shared/Stepper";
import { cn } from "@/lib/utils";
import type { Event } from "@/types/api";

type Tone = "done" | "active" | "fail" | "reject" | "need" | "neutral";

interface Described {
  title: string;
  tone: Tone;
  icon: LucideIcon;
  reason: string;
  note: string;
}

const TONE_DOT: Record<Tone, string> = {
  done: "border-status-done-edge bg-status-done-edge text-on-status",
  active: "border-status-active-edge text-status-active-edge",
  fail: "border-secondary-foreground text-foreground",
  reject: "border-secondary-foreground text-foreground",
  need: "border-attention bg-attention-bg text-attention",
  neutral: "border-border-strong text-muted-foreground",
};

const TONE_TEXT: Partial<Record<Tone, string>> = {
  fail: "font-semibold",
  reject: "font-semibold",
  need: "font-semibold text-attention",
};

function kindLabel(kind: string): string {
  const key = EVENT_KIND_KEYS[kind];
  return key ? t(key) : kind;
}

/** One journal row as a sentence: what happened, how it went, how long it took, and the reason if one was given. */
export function describeEvent(event: Event): Described {
  const stage = stageLabel(event.stage);
  const reason = event.detail.reason ?? "";
  const base = { reason, note: "" };
  switch (event.kind) {
    case "gate": {
      const passed = event.outcome === "pass";
      return {
        ...base,
        title: t(passed ? "shell.timeline.gatePassed" : "shell.timeline.gateFailed", { stage }),
        tone: passed ? "done" : "fail",
        icon: passed ? Check : X,
        note: event.detail.duration_ms === undefined ? "" : ms(event.detail.duration_ms),
      };
    }
    case "approved":
      return { ...base, title: t("shell.timeline.approved", { checkpoint: event.outcome }), tone: "done", icon: User };
    case "rejected":
      return { ...base, title: t("shell.timeline.rejected", { stage }), tone: "reject", icon: Undo2 };
    case "parked":
      return { ...base, title: t("shell.timeline.parked", { stage }), tone: "need", icon: User };
    case "blocked":
      return { ...base, title: t("shell.timeline.blocked", { stage }), tone: "need", icon: User };
    case "transition":
      return {
        ...base,
        title: t("shell.timeline.transition", { from: stageLabel(event.from_stage), stage }),
        tone: "active",
        icon: Circle,
      };
    case "merged":
      return { ...base, title: t("shell.timeline.merged"), tone: "done", icon: Check };
    case "continued":
    case "resumed":
      return { ...base, title: kindLabel(event.kind), tone: "active", icon: Circle };
    default:
      return { ...base, title: event.stage ? `${kindLabel(event.kind)}: ${stage}` : kindLabel(event.kind), tone: "neutral", icon: Circle };
  }
}

export interface EventTimelineProps {
  /** Rows in the order to show them; the component does not sort. */
  events: Event[];
  /** Accessible name of the list. */
  label?: string;
  className?: string;
}

/** The history of a task as a vertical timeline (FR-98): gates with result and duration, approvals, rejections with reason, parks, merge. */
export function EventTimeline({ events, label, className }: EventTimelineProps) {
  return (
    <ol aria-label={label ?? t("shell.timeline.label")} className={cn("m-0 list-none p-0", className)}>
      {events.map((event, index) => {
        const row = describeEvent(event);
        const Icon = row.icon;
        return (
          <li
            key={event.id}
            data-kind={event.kind}
            className="grid grid-cols-[1.375rem_minmax(0,1fr)] items-start gap-x-3 py-1.5 text-[13px] shell:grid-cols-[7rem_1.375rem_minmax(0,1fr)_auto]"
          >
            <span className="col-span-2 font-mono text-xs text-muted-foreground shell:col-span-1 shell:pt-0.5">{stamp(event.at)}</span>
            <span
              aria-hidden="true"
              className={cn(
                "relative grid size-5 place-items-center rounded-full border-[1.5px] bg-card",
                TONE_DOT[row.tone],
                index < events.length - 1 && "after:absolute after:top-5 after:left-1/2 after:h-3.5 after:border-l after:border-border",
              )}
            >
              <Icon className="size-[11px]" strokeWidth={2.2} />
            </span>
            <span className={cn("min-w-0 wrap-anywhere", TONE_TEXT[row.tone])}>
              {row.title}
              {row.reason ? <span className="mt-0.5 block max-w-3xl text-[12.5px] font-normal text-muted-foreground">{row.reason}</span> : null}
            </span>
            {row.note ? <span className="col-start-2 whitespace-nowrap text-xs text-muted-foreground shell:col-start-auto shell:pt-0.5">{row.note}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Folder } from "@/types/api";

/** One status palette everywhere: backlog gray, active blue, done green. Orange (`need`) is "Needs you" only. */
export type StatusKind = Folder | "need";

const STYLES: Record<StatusKind, { chip: string; dot: string }> = {
  backlog: { chip: "bg-status-backlog-bg text-status-backlog-fg ring-status-backlog-edge/35", dot: "bg-status-backlog-edge" },
  active: { chip: "bg-status-active-bg text-status-active-fg ring-status-active-edge/35", dot: "bg-status-active-edge" },
  done: { chip: "bg-status-done-bg text-status-done-fg ring-status-done-edge/35", dot: "bg-status-done-edge" },
  need: { chip: "bg-attention-bg text-attention ring-attention-line", dot: "bg-attention" },
};

/** The status of a task from its folder; an unknown folder reads as backlog. */
export function statusOfFolder(folder: Folder | ""): Folder {
  return folder === "" ? "backlog" : folder;
}

export interface StatusChipProps {
  status: StatusKind;
  /** Replaces the default text ("Backlog", "Active", "Done", "Needs you"). The text is always shown: status is never color alone (FR-91). */
  label?: string;
  className?: string;
}

export function StatusChip({ status, label, className }: StatusChipProps) {
  const style = STYLES[status];
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex h-5 max-w-full items-center gap-1.5 rounded-[5px] px-2 text-[11px] font-semibold whitespace-nowrap ring-1 ring-inset",
        style.chip,
        className,
      )}
    >
      <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", style.dot)} />
      <span className="truncate" title={label}>
        {label ?? t(`chip.status.${status}`)}
      </span>
    </span>
  );
}

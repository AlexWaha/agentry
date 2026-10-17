import { Link } from "@tanstack/react-router";

import { Chip } from "@/components/shared/Chip";
import { Button } from "@/components/ui/button";
import { taskPath } from "@/config/routes";
import { t } from "@/i18n";

import { kindLabel } from "./kinds";
import type { SimNode } from "./layout";

export interface GraphDetailsProps {
  node: SimNode;
  onClear: () => void;
}

/** The strip under the drawing for the selected node: what it is, how many links it has, where it leads. */
export function GraphDetails({ node, onClear }: GraphDetailsProps) {
  return (
    <section
      aria-label={t("graphs.details.title")}
      aria-live="polite"
      className="mt-3 flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-border px-3 py-2"
    >
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
        <Chip>{kindLabel(node.kind)}</Chip>
        <span className="font-mono text-[12.5px] wrap-anywhere">{node.label}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{t("graphs.details.links", { links: node.degree })}</span>
        {node.kind === "task" ? (
          <Link to={taskPath(node.label)} className="text-xs underline underline-offset-2">
            {t("graphs.details.openTask", { task: node.label })}
          </Link>
        ) : null}
      </p>
      <Button size="sm" variant="ghost" onClick={onClear}>
        {t("graphs.details.clear")}
      </Button>
    </section>
  );
}

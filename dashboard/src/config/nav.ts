import {
  Activity,
  Brain,
  ChartColumn,
  Columns3,
  LayoutGrid,
  ListChecks,
  Network,
  Puzzle,
  ScrollText,
  User,
  type LucideIcon,
} from "lucide-react";

import type { PageId } from "@/config/routes";
import { ROUTES } from "@/config/routes";

export interface NavEntry {
  id: PageId;
  to: string;
  /** i18n key of the label. */
  labelKey: string;
  icon: LucideIcon;
}

/** The ten sidebar entries, in display order (FR-76). */
export const NAV_ENTRIES: readonly NavEntry[] = [
  { id: "overview", to: ROUTES.overview, labelKey: "shell.nav.overview", icon: LayoutGrid },
  { id: "needs", to: ROUTES.needs, labelKey: "shell.nav.needs", icon: User },
  { id: "board", to: ROUTES.board, labelKey: "shell.nav.board", icon: Columns3 },
  { id: "tasks", to: ROUTES.tasks, labelKey: "shell.nav.tasks", icon: ListChecks },
  { id: "activity", to: ROUTES.activity, labelKey: "shell.nav.activity", icon: Activity },
  { id: "metrics", to: ROUTES.metrics, labelKey: "shell.nav.metrics", icon: ChartColumn },
  { id: "memory", to: ROUTES.memory, labelKey: "shell.nav.memory", icon: Brain },
  { id: "graphs", to: ROUTES.graphs, labelKey: "shell.nav.graphs", icon: Network },
  { id: "harness", to: ROUTES.harness, labelKey: "shell.nav.harness", icon: Puzzle },
  { id: "handoffs", to: ROUTES.handoffs, labelKey: "shell.nav.handoffs", icon: ScrollText },
];

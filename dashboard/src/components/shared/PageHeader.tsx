import type { ReactNode } from "react";

import { DocsLink } from "@/components/shared/DocsLink";
import type { PageId } from "@/config/routes";

export interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  /** Adds the "Learn more" link of this page (FR-112). */
  docsPage?: PageId;
}

/** The h1, one-line description and docs link at the top of every page. */
export function PageHeader({ title, subtitle, docsPage }: PageHeaderProps) {
  return (
    <header className="mb-5">
      <h1 className="text-[22px] font-semibold tracking-tight wrap-anywhere">{title}</h1>
      {subtitle ? <p className="mt-0.5 text-muted-foreground wrap-anywhere">{subtitle}</p> : null}
      {docsPage ? <DocsLink page={docsPage} className="mt-2" /> : null}
    </header>
  );
}

export interface SectionHeadProps {
  title: string;
  /** A link or note aligned to the right of the title. */
  right?: ReactNode;
}

export function SectionHead({ title, right }: SectionHeadProps) {
  return (
    <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-x-3">
      <h2 className="text-[13px] font-semibold text-secondary-foreground">{title}</h2>
      {right ? <div className="text-[12.5px] text-muted-foreground">{right}</div> : null}
    </div>
  );
}

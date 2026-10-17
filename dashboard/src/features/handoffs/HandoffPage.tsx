import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { NotFound } from "@/components/shared/NotFound";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";
import { ApiError } from "@/lib/api-client";
import { bytes } from "@/lib/format";
import { handoffQuery } from "@/lib/queries";
import { Markdown } from "@/lib/markdown";
import type { HandoffDoc } from "@/types/api";

import { ValidityChip } from "./ValidityChip";

const HTTP_NOT_FOUND = 404;
const HTTP_INVALID = 422;

function Problems({ problems }: { problems: string[] }) {
  return (
    <Card className="mb-4 px-4 py-3">
      <h2 className="font-semibold">{t("handoffs.reader.problems")}</h2>
      <ul className="mt-1.5 list-disc pl-5">
        {problems.map((problem, index) => (
          <li key={`${index}:${problem}`} className="wrap-anywhere">
            {problem}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Document({ doc }: { doc: HandoffDoc }) {
  return (
    <>
      <PageHeader
        title={t("handoffs.reader.title", { task: doc.task })}
        subtitle={doc.title || t("handoffs.untitled")}
        docsPage="handoff"
      />
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
        <ValidityChip valid={doc.valid} />
        <span className="min-w-0 wrap-anywhere">{t("handoffs.by", { author: doc.author })}</span>
        <time dateTime={doc.date} className="tabular-nums">
          {doc.date}
        </time>
        <span className="tabular-nums">{bytes(doc.bytes)}</span>
      </div>
      {!doc.valid && doc.problems.length > 0 ? <Problems problems={doc.problems} /> : null}
      <Card className="min-w-0 px-5 py-4">
        {doc.body.trim() === "" ? <EmptyState title={t("handoffs.reader.empty")} /> : <Markdown source={doc.body} />}
      </Card>
    </>
  );
}

/** The reader of one handoff document (FR-111): the body goes through the safe markdown, `problems` are listed. */
export function HandoffPage() {
  const { taskId = "" } = useParams({ strict: false });
  const query = useQuery(handoffQuery(taskId));

  let body: ReactNode;
  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    const missing = status === HTTP_NOT_FOUND || status === HTTP_INVALID;
    body = missing ? (
      <NotFound
        title={t("handoffs.reader.notFound.title")}
        message={t("handoffs.reader.notFound.message", { task: taskId })}
      />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  } else if (query.data === undefined) {
    body = (
      <div role="status" aria-label={t("handoffs.reader.loading")} className="grid gap-2">
        <Skeleton className="h-8 w-60" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  } else {
    body = <Document doc={query.data} />;
  }

  return (
    <>
      <Link to={ROUTES.handoffs} className="mb-3 inline-block text-muted-foreground underline underline-offset-2 hover:text-foreground">
        {t("handoffs.reader.back")}
      </Link>
      {body}
    </>
  );
}

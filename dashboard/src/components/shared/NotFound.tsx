import { Link } from "@tanstack/react-router";

import { Card } from "@/components/ui/card";
import { ROUTES } from "@/config/routes";
import { t } from "@/i18n";

export interface NotFoundProps {
  /** Replaces "Page not found", for example "Task not found". */
  title?: string;
  /** Replaces the default explanation. */
  message?: string;
}

/** The 404 body: used for a path the router does not know and for an id the API answered 404 for (FR-97). */
export function NotFound({ title, message }: NotFoundProps) {
  return (
    <Card className="max-w-xl px-5 py-6">
      <h1 className="text-lg font-semibold">{title ?? t("error.notFound.title")}</h1>
      <p className="mt-1.5 text-muted-foreground">{message ?? t("error.notFound.message")}</p>
      <Link to={ROUTES.overview} className="mt-3 inline-block text-foreground underline underline-offset-2">
        {t("error.notFound.back")}
      </Link>
    </Card>
  );
}

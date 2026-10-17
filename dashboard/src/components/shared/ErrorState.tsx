import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { ApiError, NetworkError } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const HTTP_UNAVAILABLE = 503;

export interface ErrorStateProps {
  /** The error of the failed query. */
  error: unknown;
  /** Called by the Retry control; normally the query's `refetch`. */
  onRetry: () => void;
  className?: string;
}

/** The error state of a page: a message and a retry control. A 503 reads as a transient notice (FR-89). */
export function ErrorState({ error, onRetry, className }: ErrorStateProps) {
  const message =
    error instanceof ApiError && error.status === HTTP_UNAVAILABLE
      ? t("error.unavailable")
      : error instanceof NetworkError
        ? t("error.network")
        : error instanceof ApiError
          ? error.message
          : t("error.generic");
  return (
    <div role="alert" className={cn("flex flex-wrap items-center justify-between gap-3 px-4 py-5", className)}>
      <span className="min-w-0 wrap-anywhere text-secondary-foreground">{message}</span>
      <Button size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}

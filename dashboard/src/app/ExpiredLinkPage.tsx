import { Card } from "@/components/ui/card";
import { t } from "@/i18n";

/** Shown instead of the page when the API answers 401: the launch token is gone or the server restarted (FR-79). */
export function ExpiredLinkPage() {
  return (
    <Card className="max-w-xl px-5 py-6" role="alert">
      <h1 className="text-lg font-semibold">{t("error.expired.title")}</h1>
      <p className="mt-1.5 text-muted-foreground">{t("error.expired.action")}</p>
    </Card>
  );
}

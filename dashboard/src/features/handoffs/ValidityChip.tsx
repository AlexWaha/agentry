import { StatusChip } from "@/components/shared/StatusChip";
import { t } from "@/i18n";

/** Valid is green; an invalid document is a neutral chip with its text, never colour alone (FR-91). */
export function ValidityChip({ valid }: { valid: boolean }) {
  return valid ? (
    <StatusChip status="done" label={t("handoffs.valid")} />
  ) : (
    <StatusChip status="backlog" label={t("handoffs.invalid")} />
  );
}

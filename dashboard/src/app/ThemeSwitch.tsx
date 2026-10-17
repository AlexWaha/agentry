import { t } from "@/i18n";
import { THEME_CHOICES, useTheme, type ThemeChoice } from "@/lib/theme";
import { cn } from "@/lib/utils";

const THEME_LABEL_KEYS: Record<ThemeChoice, string> = {
  light: "theme.light",
  dark: "theme.dark",
  system: "theme.system",
};

/** Light, Dark or System as one radio group (FR-84). The native radios keep arrow-key and Tab behaviour. */
export function ThemeSwitch() {
  const { choice, setChoice } = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label={t("theme.label")}
      className="grid grid-cols-3 gap-0.5 rounded-lg border border-border-control bg-muted p-0.5"
    >
      {THEME_CHOICES.map((value) => (
        <label key={value} className="relative">
          <input
            type="radio"
            name="theme"
            value={value}
            checked={choice === value}
            onChange={() => setChoice(value)}
            className="peer absolute inset-0 m-0 cursor-pointer opacity-0"
          />
          <span
            className={cn(
              "block rounded-md px-2.5 py-1 text-center text-xs text-muted-foreground peer-hover:text-foreground",
              "peer-checked:bg-background peer-checked:font-semibold peer-checked:text-foreground peer-checked:ring-1 peer-checked:ring-border-strong",
              "peer-focus-visible:outline-2 peer-focus-visible:outline-ring",
            )}
          >
            {t(THEME_LABEL_KEYS[value])}
          </span>
        </label>
      ))}
    </div>
  );
}

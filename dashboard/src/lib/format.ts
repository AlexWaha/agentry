import { t } from "@/i18n";

const MS_PER_S = 1000;
const S_PER_MIN = 60;
const S_PER_HOUR = 3600;
const S_PER_DAY = 86400;
const BYTES_PER_KB = 1024;
const BYTES_PER_MB = BYTES_PER_KB * 1024;
const NO_VALUE = "-";

const pad = (n: number): string => String(n).padStart(2, "0");

/** Seconds as the two most useful units: 45s, 3m 05s, 2h 10m, 1d 4h. Null or undefined is "-". */
export function dur(seconds: number | null | undefined): string {
  if (seconds == null) return NO_VALUE;
  const s = Math.round(seconds);
  if (s < 1) return t("common.underOneSecond");
  if (s < S_PER_MIN) return `${s}s`;
  if (s < S_PER_HOUR) return `${Math.floor(s / S_PER_MIN)}m ${pad(s % S_PER_MIN)}s`;
  if (s < S_PER_DAY) return `${Math.floor(s / S_PER_HOUR)}h ${pad(Math.floor((s % S_PER_HOUR) / S_PER_MIN))}m`;
  return `${Math.floor(s / S_PER_DAY)}d ${Math.floor((s % S_PER_DAY) / S_PER_HOUR)}h`;
}

/** Milliseconds: under a second stays in ms, longer goes through dur(). */
export function ms(value: number | null | undefined): string {
  if (value == null) return NO_VALUE;
  return value < MS_PER_S ? `${value}ms` : dur(value / MS_PER_S);
}

/** An ISO timestamp as "MM-DD HH:MM:SSZ" (the server sends UTC with a Z suffix). */
export function stamp(iso: string): string {
  if (!iso) return NO_VALUE;
  return `${iso.slice(5, 10)} ${iso.slice(11, 19)}Z`;
}

/** The UTC clock part of an ISO timestamp: "HH:MM:SS". */
export function clock(iso: string): string {
  return iso ? iso.slice(11, 19) : NO_VALUE;
}

/** How long ago an ISO timestamp was, as "3m 05s ago". `now` is injectable for tests. */
export function ago(iso: string, now: number = Date.now()): string {
  if (!iso) return NO_VALUE;
  const seconds = Math.max(0, (now - Date.parse(iso)) / MS_PER_S);
  return t("common.ago", { duration: dur(seconds) });
}

/** A byte count as B, KB or MB. */
export function bytes(n: number | null | undefined): string {
  if (n == null) return NO_VALUE;
  if (n < BYTES_PER_KB) return `${n} B`;
  if (n < BYTES_PER_MB) return `${(n / BYTES_PER_KB).toFixed(1)} KB`;
  return `${(n / BYTES_PER_MB).toFixed(2)} MB`;
}

export const capitalize = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

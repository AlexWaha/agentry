/** The only two keys the app writes to localStorage: the launch token and the theme choice. No locale key. */
export const STORAGE_KEYS = {
  token: "agentry-dashboard-token",
  theme: "agentry-dashboard-theme",
} as const;

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

// Dev-only: `npm run dev` proxies the API and the WebSocket to a running dashboard server.
// Start the server with `python plugin/tools/dashboard/server.py --no-open` and set
// DASHBOARD_ORIGIN to the printed origin (default is the lowest port the server prefers).
const target = process.env.DASHBOARD_ORIGIN ?? "http://127.0.0.1:41000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    proxy: {
      "/api": { target, changeOrigin: true, headers: { Origin: target } },
      "/ws": { target: target.replace(/^http/, "ws"), ws: true, changeOrigin: true, headers: { Origin: target } },
    },
  },
  build: {
    outDir: "../plugin/tools/dashboard/dist",
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    unstubGlobals: true,
    css: false,
    // No persistent on-disk module cache. The cache froze the transformed src/i18n/index.ts, so its
    // import.meta.glob("./messages/*.ts") kept the expansion from when only shell.ts existed and the message
    // files added later were missing in tests (page tests rendered raw keys).
    fsModuleCache: false,
  },
});

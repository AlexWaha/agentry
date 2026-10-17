import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import path from "node:path";
import tseslint from "typescript-eslint";

/*
 * Import boundaries (frontend-standards, FR-75). One direction only:
 *   shared (components, lib, config, types, i18n) -> features/<x> -> app
 * A feature imports only shared code and itself. Shared code imports no feature. app/ composes features
 * and test/ may reach into any of them. Implemented as a local rule so no third-party plugin is needed.
 */
const FEATURE_PATH = /(?:^|\/)src\/features\/([^/]+)(?:\/|$)/;
const FREE_AREAS = /(?:^|\/)src\/(?:app|test)\//;

function featureOf(filePath) {
  const match = FEATURE_PATH.exec(filePath.replaceAll("\\", "/"));
  return match ? match[1] : null;
}

function importedFeature(specifier, importerFile) {
  if (specifier.startsWith("@/features/")) return specifier.slice("@/features/".length).split("/")[0] || null;
  if (specifier.startsWith(".")) {
    const resolved = path.posix.resolve(path.posix.dirname(importerFile.replaceAll("\\", "/")), specifier);
    return featureOf(resolved);
  }
  return null;
}

const boundaries = {
  rules: {
    "no-cross-feature-import": {
      meta: {
        type: "problem",
        schema: [],
        messages: {
          crossFeature: "A feature must not import another feature ({{ target }}); move shared code to components, lib or config.",
          sharedToFeature: "Shared code must not import a feature ({{ target }}); features depend on shared code, not the reverse.",
        },
      },
      create(context) {
        const file = context.filename.replaceAll("\\", "/");
        if (FREE_AREAS.test(file) || /(?:^|\/)src\/main\.tsx$/.test(file)) return {};
        const own = featureOf(file);
        const check = (node, source) => {
          if (typeof source !== "string") return;
          const target = importedFeature(source, file);
          if (!target || target === own) return;
          context.report({ node, messageId: own ? "crossFeature" : "sharedToFeature", data: { target } });
        };
        return {
          ImportDeclaration: (node) => check(node, node.source.value),
          ExportNamedDeclaration: (node) => node.source && check(node, node.source.value),
          ExportAllDeclaration: (node) => check(node, node.source.value),
          ImportExpression: (node) => node.source.type === "Literal" && check(node, node.source.value),
        };
      },
    },
  },
};

export default defineConfig(
  globalIgnores(["node_modules", "coverage", "dist"]),
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, boundaries },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "boundaries/no-cross-feature-import": "error",
      "no-console": "error",
      "no-debugger": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "no-restricted-syntax": [
        "error",
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: "Untrusted text is never rendered as HTML (FR-87)." },
        { selector: "Property[key.name='refetchInterval']", message: "Data refreshes through WebSocket invalidation only (FR-81)." },
      ],
    },
  },
  {
    files: ["public/**/*.js"],
    languageOptions: { sourceType: "script", globals: globals.browser },
  },
  {
    files: ["vite.config.ts", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}"],
    rules: { "no-restricted-syntax": "off" },
  },
);

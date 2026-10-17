import { DOCS_BLOB_URL } from "@/config/constants";
import links from "@/config/docs-links.json";
import type { PageId } from "@/config/routes";

interface DocsLinkEntry {
  path: string;
  anchor: string;
}

const entries: Record<string, DocsLinkEntry> = links;

/** `https://github.com/AlexWaha/agentry/blob/main/<path>` plus `#<anchor>` when the anchor is not empty (FR-112). */
export function docsUrl(page: PageId): string {
  const entry = entries[page];
  if (!entry) return DOCS_BLOB_URL;
  const base = `${DOCS_BLOB_URL}/${entry.path}`;
  return entry.anchor ? `${base}#${entry.anchor}` : base;
}

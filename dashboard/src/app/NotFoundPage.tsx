import { NotFound } from "@/components/shared/NotFound";

/** Rendered by the root route for any path that is not one of the twelve pages (FR-78). */
export function NotFoundPage() {
  return <NotFound />;
}

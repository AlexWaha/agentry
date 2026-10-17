import { pageParam, type PageSearch } from "@/lib/search-params";

/** Route search validation of the list: `page` is kept only when it is 2 or more. */
export function validateHandoffsSearch(raw: Record<string, unknown>): PageSearch {
  return { page: pageParam(raw.page) };
}

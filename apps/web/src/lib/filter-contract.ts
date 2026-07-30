import { z } from "zod"

import { SearchFoldV1Error, setRawSearchParamV1 } from "./search-fold-v1"

const FilterSchema = z
  .object({
    country: z.enum(["all", "kr", "us"]).catch("all"),
    sourceId: z.union([z.literal(""), z.string().uuid()]).catch(""),
    keyword: z.string().trim().max(300).catch(""),
    search: z.string().catch(""),
    period: z.enum(["24h", "7d", "14d", "30d", "90d"]).catch("30d"),
    page: z.coerce.number().int().min(1).catch(1),
  })
  .strict()

export type DashboardFilters = Omit<z.infer<typeof FilterSchema>, "search" | "page"> & {
  readonly search?: string
  readonly page?: number
}

export const DEFAULT_FILTERS = {
  country: "all",
  sourceId: "",
  keyword: "",
  search: "",
  period: "30d",
  page: 1,
} as const satisfies DashboardFilters

export const POST_PAGE_SIZE = 50

function parseSearch(rawSearch: string): string {
  if (rawSearch.length === 0) {
    return ""
  }
  try {
    setRawSearchParamV1(new URLSearchParams(), rawSearch)
    return rawSearch
  } catch (error) {
    if (error instanceof SearchFoldV1Error) {
      return ""
    }
    throw error
  }
}

export function parseFilters(searchParams: URLSearchParams): DashboardFilters {
  const parsed = FilterSchema.parse({
    country: searchParams.get("country") ?? DEFAULT_FILTERS.country,
    sourceId: searchParams.get("source_id") ?? DEFAULT_FILTERS.sourceId,
    keyword: searchParams.get("keyword") ?? DEFAULT_FILTERS.keyword,
    search: searchParams.get("search") ?? DEFAULT_FILTERS.search,
    period: searchParams.get("period") ?? DEFAULT_FILTERS.period,
    page: searchParams.get("page") ?? DEFAULT_FILTERS.page,
  })
  return { ...parsed, search: parseSearch(parsed.search) }
}

export type PageSearchParams = Readonly<Record<string, string | readonly string[] | undefined>>

function first(value: string | readonly string[] | undefined): string | undefined {
  return typeof value === "string" ? value : value?.at(0)
}

export function parsePageFilters(
  values: PageSearchParams,
  _legacyDefaultPeriod?: DashboardFilters["period"],
): DashboardFilters {
  const searchParams = new URLSearchParams()
  for (const key of ["country", "source_id", "keyword", "search", "period", "page"] as const) {
    const value = first(values[key])
    if (value !== undefined) {
      searchParams.set(key, value)
    }
  }
  return parseFilters(searchParams)
}

const PERIOD_MILLISECONDS = {
  "24h": 24 * 60 * 60 * 1_000,
  "7d": 7 * 24 * 60 * 60 * 1_000,
  "14d": 14 * 24 * 60 * 60 * 1_000,
  "30d": 30 * 24 * 60 * 60 * 1_000,
  "90d": 90 * 24 * 60 * 60 * 1_000,
} as const

export function filtersToSearchParams(
  filters: DashboardFilters,
  now: Date = new Date(),
): URLSearchParams {
  const searchParams = new URLSearchParams()
  if (filters.country !== "all") {
    searchParams.set("country", filters.country)
  }
  if (filters.sourceId.length > 0) {
    searchParams.set("source_id", filters.sourceId)
  }
  if (filters.keyword.length > 0) {
    searchParams.set("keyword", filters.keyword)
  }
  if (filters.search !== undefined && filters.search.length > 0) {
    setRawSearchParamV1(searchParams, filters.search)
  }
  const publishedTo = now.getTime()
  const publishedFrom = publishedTo - PERIOD_MILLISECONDS[filters.period]
  searchParams.set("published_from", new Date(publishedFrom).toISOString())
  searchParams.set("published_to", new Date(publishedTo).toISOString())
  return searchParams
}

export function filtersToPageSearchParams(filters: DashboardFilters): URLSearchParams {
  const searchParams = new URLSearchParams()
  if (filters.country !== DEFAULT_FILTERS.country) {
    searchParams.set("country", filters.country)
  }
  if (filters.sourceId.length > 0) {
    searchParams.set("source_id", filters.sourceId)
  }
  if (filters.keyword.length > 0) {
    searchParams.set("keyword", filters.keyword)
  }
  if (filters.search !== undefined && filters.search.length > 0) {
    setRawSearchParamV1(searchParams, filters.search)
  }
  if (filters.period !== DEFAULT_FILTERS.period) {
    searchParams.set("period", filters.period)
  }
  if ((filters.page ?? DEFAULT_FILTERS.page) > DEFAULT_FILTERS.page) {
    searchParams.set("page", (filters.page ?? DEFAULT_FILTERS.page).toString())
  }
  return searchParams
}

function relativeUrl(pathname: string, searchParams: URLSearchParams): string {
  const safePathname = pathname.startsWith("/") && !pathname.startsWith("//") ? pathname : "/"
  const query = searchParams.toString()
  return query.length === 0 ? safePathname : `${safePathname}?${query}`
}

export function paginationUrls(
  pathname: string,
  filters: DashboardFilters,
  totalPages: number,
): { readonly previous: string | null; readonly next: string | null } {
  const boundedTotalPages = Number.isSafeInteger(totalPages) && totalPages > 0 ? totalPages : 1
  const boundedPage = Math.min(filters.page ?? DEFAULT_FILTERS.page, boundedTotalPages)
  const previous =
    boundedPage > 1
      ? relativeUrl(pathname, filtersToPageSearchParams({ ...filters, page: boundedPage - 1 }))
      : null
  const next =
    boundedPage < boundedTotalPages
      ? relativeUrl(pathname, filtersToPageSearchParams({ ...filters, page: boundedPage + 1 }))
      : null
  return { previous, next }
}

export function reportSearchParams(): URLSearchParams {
  return new URLSearchParams({ page: "1", page_size: "30" })
}

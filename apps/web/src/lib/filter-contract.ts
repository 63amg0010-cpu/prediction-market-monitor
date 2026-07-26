import { z } from "zod"

const FilterSchema = z
  .object({
    country: z.enum(["all", "kr", "us"]).catch("all"),
    sourceId: z.union([z.literal(""), z.string().uuid()]).catch(""),
    keyword: z.string().trim().max(300).catch(""),
    period: z.enum(["24h", "7d", "14d", "30d", "90d"]).catch("7d"),
  })
  .strict()

export type DashboardFilters = z.infer<typeof FilterSchema>

export const DEFAULT_FILTERS = {
  country: "all",
  sourceId: "",
  keyword: "",
  period: "7d",
} as const satisfies DashboardFilters

export function parseFilters(
  searchParams: URLSearchParams,
  defaultPeriod: DashboardFilters["period"] = DEFAULT_FILTERS.period,
): DashboardFilters {
  return FilterSchema.parse({
    country: searchParams.get("country") ?? DEFAULT_FILTERS.country,
    sourceId: searchParams.get("source_id") ?? DEFAULT_FILTERS.sourceId,
    keyword: searchParams.get("keyword") ?? DEFAULT_FILTERS.keyword,
    period: searchParams.get("period") ?? defaultPeriod,
  })
}

export type PageSearchParams = Readonly<Record<string, string | readonly string[] | undefined>>

function first(value: string | readonly string[] | undefined): string | undefined {
  return typeof value === "string" ? value : value?.at(0)
}

export function parsePageFilters(
  values: PageSearchParams,
  defaultPeriod: DashboardFilters["period"] = DEFAULT_FILTERS.period,
): DashboardFilters {
  const searchParams = new URLSearchParams()
  for (const key of ["country", "source_id", "keyword", "period"] as const) {
    const value = first(values[key])
    if (value !== undefined) {
      searchParams.set(key, value)
    }
  }
  return parseFilters(searchParams, defaultPeriod)
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
  if (filters.period !== DEFAULT_FILTERS.period) {
    searchParams.set("period", filters.period)
  }
  return searchParams
}

export function reportSearchParams(): URLSearchParams {
  return new URLSearchParams({ page: "1", page_size: "30" })
}

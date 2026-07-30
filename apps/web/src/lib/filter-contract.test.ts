import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { describe, expect, it } from "vitest"
import { z } from "zod"

import {
  filtersToPageSearchParams,
  filtersToSearchParams,
  POST_PAGE_SIZE,
  paginationUrls,
  parsePageFilters,
} from "./filter-contract"

const OpenApiDashboardPathSchema = z.object({
  paths: z.object({
    "/v1/dashboard": z.object({
      get: z.object({
        parameters: z.array(z.object({ in: z.string(), name: z.string() }).passthrough()),
      }),
    }),
  }),
})

const filters = {
  country: "kr",
  sourceId: "11111111-1111-4111-8111-111111111111",
  keyword: "rate cuts",
  search: "Rate % _ \\",
  period: "30d",
  page: 2,
} as const

describe("dashboard filter serialization", () => {
  it("maps the page filter model to only the FastAPI query names", () => {
    const query = filtersToSearchParams(filters, new Date("2026-07-22T03:00:00Z"))

    expect(Object.fromEntries(query)).toEqual({
      country: "kr",
      source_id: "11111111-1111-4111-8111-111111111111",
      keyword: "rate cuts",
      search: "Rate % _ \\",
      published_from: "2026-06-22T03:00:00.000Z",
      published_to: "2026-07-22T03:00:00.000Z",
    })
    expect(query.has("community")).toBe(false)
    expect(query.has("period")).toBe(false)
  })

  it("round-trips the source id used by the page URL", () => {
    expect(
      parsePageFilters({
        country: "kr",
        source_id: "11111111-1111-4111-8111-111111111111",
        keyword: "rate cuts",
        search: "Rate % _ \\",
        period: "30d",
        page: "2",
      }),
    ).toEqual(filters)
  })

  it("defaults dashboard and posts filters to 30 days and page one", () => {
    const parsed = parsePageFilters({}, "90d")
    const query = filtersToSearchParams(parsed, new Date("2026-07-26T15:00:00Z"))

    expect(parsed).toMatchObject({ period: "30d", page: 1, search: "" })
    expect(query.get("published_from")).toBe("2026-06-26T15:00:00.000Z")
    expect(query.get("published_to")).toBe("2026-07-26T15:00:00.000Z")
  })

  it("resolves malformed pages and invalid search values to the empty-safe defaults", () => {
    const tooLongSearch = "a".repeat(101)

    expect(parsePageFilters({ page: "-4", search: "x" })).toMatchObject({ page: 1, search: "" })
    expect(parsePageFilters({ page: "not-a-page", search: tooLongSearch })).toMatchObject({
      page: 1,
      search: "",
    })
  })

  it("omits page on a filter submission URL and retains the raw validated search value", () => {
    const query = filtersToPageSearchParams({ ...filters, page: 1 })

    expect(Object.fromEntries(query)).toEqual({
      country: "kr",
      source_id: "11111111-1111-4111-8111-111111111111",
      keyword: "rate cuts",
      search: "Rate % _ \\",
    })
    expect(query.has("page")).toBe(false)
  })

  it("creates bounded previous and next URLs that preserve search and keyword", () => {
    expect(POST_PAGE_SIZE).toBe(50)
    expect(paginationUrls("/posts", filters, 3)).toEqual({
      previous:
        "/posts?country=kr&source_id=11111111-1111-4111-8111-111111111111&keyword=rate+cuts&search=Rate+%25+_+%5C",
      next: "/posts?country=kr&source_id=11111111-1111-4111-8111-111111111111&keyword=rate+cuts&search=Rate+%25+_+%5C&page=3",
    })
    expect(paginationUrls("/posts", { ...filters, page: 99 }, 3)).toEqual({
      previous:
        "/posts?country=kr&source_id=11111111-1111-4111-8111-111111111111&keyword=rate+cuts&search=Rate+%25+_+%5C&page=2",
      next: null,
    })
  })

  it("matches the generated OpenAPI dashboard query keys", () => {
    const raw: unknown = JSON.parse(
      readFileSync(resolve(process.cwd(), "../api/openapi.json"), "utf8"),
    )
    const openapi = OpenApiDashboardPathSchema.parse(raw)
    const openApiKeys = openapi.paths["/v1/dashboard"].get.parameters
      .filter((parameter) => parameter.in === "query")
      .map((parameter) => parameter.name)

    const queryKeys = [...filtersToSearchParams(filters, new Date("2026-07-22T03:00:00Z")).keys()]

    expect(queryKeys).toEqual(openApiKeys)
  })
})

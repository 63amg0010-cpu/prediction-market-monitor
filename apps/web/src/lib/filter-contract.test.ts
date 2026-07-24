import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { describe, expect, it } from "vitest"
import { z } from "zod"

import { filtersToSearchParams, parsePageFilters } from "./filter-contract"

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
  period: "24h",
} as const

describe("dashboard filter serialization", () => {
  it("maps the page filter model to only the FastAPI query names", () => {
    const query = filtersToSearchParams(filters, new Date("2026-07-22T03:00:00Z"))

    expect(Object.fromEntries(query)).toEqual({
      country: "kr",
      source_id: "11111111-1111-4111-8111-111111111111",
      keyword: "rate cuts",
      published_from: "2026-07-21T03:00:00.000Z",
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
        period: "24h",
      }),
    ).toEqual(filters)
  })

  it("matches the generated OpenAPI dashboard query keys", () => {
    const raw: unknown = JSON.parse(
      readFileSync(resolve(process.cwd(), "../api/openapi.json"), "utf8"),
    )
    const openapi = OpenApiDashboardPathSchema.parse(raw)
    const openApiKeys = openapi.paths["/v1/dashboard"].get.parameters
      .filter((parameter) => parameter.in === "query")
      .map((parameter) => parameter.name)

    expect([...filtersToSearchParams(filters, new Date("2026-07-22T03:00:00Z")).keys()]).toEqual(
      openApiKeys,
    )
  })
})

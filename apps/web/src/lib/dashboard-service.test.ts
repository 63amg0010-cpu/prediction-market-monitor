import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  FASTAPI_DASHBOARD_FIXTURE,
  FASTAPI_POST_PAGE_FIXTURE,
  FASTAPI_REPORT_PAGE_FIXTURE,
} from "../test/dashboard-fixtures"
import { loadDashboard } from "./dashboard-service"

vi.mock("server-only", () => ({}))

vi.mock("./bff-server", () => ({
  getBffReadToken: vi.fn(async () => "test-bff-token"),
}))

vi.mock("./server-env", () => ({
  readServerEnvironment: () => ({
    apiBaseUrl: "https://api.example.test",
    bffClientCredential: "unused",
    bffCredentialVersion: "unused",
    deploymentIdentity: "unused",
  }),
}))

const defaultFilters = {
  country: "all",
  sourceId: "",
  keyword: "",
  search: "",
  period: "30d",
  page: 1,
} as const

describe("dashboard BFF integration state", () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date("2026-07-22T03:00:00Z") }))

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it("returns ready when all three current FastAPI projections parse", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname
        switch (path) {
          case "/v1/dashboard":
            return Response.json(FASTAPI_DASHBOARD_FIXTURE)
          case "/v1/posts":
            return Response.json(FASTAPI_POST_PAGE_FIXTURE)
          case "/v1/reports":
            return Response.json(FASTAPI_REPORT_PAGE_FIXTURE)
          default:
            return new Response(null, { status: 404 })
        }
      }),
    )

    const result = await loadDashboard(defaultFilters)

    expect(result.kind).toBe("ready")
  })

  it("forwards search to dashboard and posts, but forwards page only to posts", async () => {
    const requests: URL[] = []
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const url = new URL(request.url)
        requests.push(url)
        switch (url.pathname) {
          case "/v1/dashboard":
            return Response.json(FASTAPI_DASHBOARD_FIXTURE)
          case "/v1/posts":
            return Response.json(FASTAPI_POST_PAGE_FIXTURE)
          case "/v1/reports":
            return Response.json(FASTAPI_REPORT_PAGE_FIXTURE)
          default:
            return new Response(null, { status: 404 })
        }
      }),
    )

    await loadDashboard({ ...defaultFilters, search: "rate %", page: 2 })

    const dashboard = requests.find((request) => request.pathname === "/v1/dashboard")
    const posts = requests.find((request) => request.pathname === "/v1/posts")
    expect(dashboard?.searchParams.get("search")).toBe("rate %")
    expect(dashboard?.searchParams.has("page")).toBe(false)
    expect(posts?.searchParams.get("search")).toBe("rate %")
    expect(posts?.searchParams.get("page")).toBe("2")
    expect(posts?.searchParams.get("page_size")).toBe("50")
  })

  it("returns invalid_request for FastAPI 422 without calling it a network outage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: [] }, { status: 422 })),
    )

    const result = await loadDashboard(defaultFilters)

    expect(result).toMatchObject({ kind: "invalid_request", retryable: false })
  })

  it("returns retryable unavailable when the network cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    )

    const result = await loadDashboard(defaultFilters)

    expect(result).toMatchObject({ kind: "unavailable", retryable: true })
  })
})

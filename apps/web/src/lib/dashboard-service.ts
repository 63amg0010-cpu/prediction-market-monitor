import "server-only"

import ky from "ky"
import type { ZodType } from "zod"

import { BoundaryError, upstreamError } from "./api-error"
import { getBffReadToken } from "./bff-server"
import {
  DashboardBundleSchema,
  DashboardResponseSchema,
  type DashboardState,
  PostPageSchema,
  ReportPageSchema,
} from "./dashboard-contract"
import {
  type DashboardFilters,
  filtersToSearchParams,
  POST_PAGE_SIZE,
  reportSearchParams,
} from "./filter-contract"
import { apiUrl, runNetworkRequest, UPSTREAM_REQUEST_TIMEOUT_MS } from "./server-http"

type ProjectionRequest<T> = {
  readonly path: string
  readonly searchParams: URLSearchParams
  readonly schema: ZodType<T>
  readonly token: string
}

async function getProjection<T>(request: ProjectionRequest<T>): Promise<T> {
  const url = apiUrl(request.path)
  url.search = request.searchParams.toString()
  const response = await runNetworkRequest(() =>
    ky.get(url, {
      timeout: UPSTREAM_REQUEST_TIMEOUT_MS,
      retry: 0,
      throwHttpErrors: false,
      headers: {
        authorization: `Bearer ${request.token}`,
        "cache-control": "no-store",
      },
    }),
  )
  if (!response.ok) {
    throw await upstreamError(response)
  }
  let raw: unknown
  try {
    raw = await response.json()
  } catch (error) {
    if (error instanceof Error) {
      throw new BoundaryError("invalid_response", "upstream returned invalid JSON")
    }
    throw error
  }
  const parsed = request.schema.safeParse(raw)
  if (!parsed.success) {
    throw new BoundaryError("invalid_response", `${request.path} response contract drifted`)
  }
  return parsed.data
}

function failureState(error: BoundaryError): DashboardState {
  if (error.status === 422) {
    return {
      kind: "invalid_request",
      reason: "필터 입력을 API가 처리할 수 없습니다. 조건을 확인해 주세요.",
      correlationId: error.correlationId,
      retryable: false,
    }
  }
  const retryable = error.code === "network" || error.status === 429 || (error.status ?? 0) >= 500
  return {
    kind: "unavailable",
    reason:
      error.code === "invalid_response"
        ? "대시보드 응답 형식이 현재 API 계약과 다릅니다."
        : "대시보드 서비스에 연결할 수 없습니다.",
    correlationId: error.correlationId,
    retryable,
  }
}

export async function loadDashboard(filters: DashboardFilters): Promise<DashboardState> {
  try {
    const token = await getBffReadToken()
    const dashboardParams = filtersToSearchParams(filters)
    const postParams = new URLSearchParams(dashboardParams)
    postParams.set("page", (filters.page ?? 1).toString())
    postParams.set("page_size", POST_PAGE_SIZE.toString())
    const [dashboard, posts, reports] = await Promise.all([
      getProjection({
        path: "/v1/dashboard",
        searchParams: dashboardParams,
        schema: DashboardResponseSchema,
        token,
      }),
      getProjection({
        path: "/v1/posts",
        searchParams: postParams,
        schema: PostPageSchema,
        token,
      }),
      getProjection({
        path: "/v1/reports",
        searchParams: reportSearchParams(),
        schema: ReportPageSchema,
        token,
      }),
    ])
    const data = DashboardBundleSchema.parse({ dashboard, posts, reports })
    return { kind: "ready", data }
  } catch (error) {
    if (error instanceof BoundaryError) {
      return failureState(error)
    }
    throw error
  }
}

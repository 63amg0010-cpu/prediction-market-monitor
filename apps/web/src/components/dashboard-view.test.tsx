import "@testing-library/jest-dom/vitest"

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { DashboardBundleSchema, type DashboardState } from "../lib/dashboard-contract"
import { FASTAPI_DASHBOARD_BUNDLE_FIXTURE } from "../test/dashboard-fixtures"
import { DashboardLoader } from "./dashboard-loader"
import { DashboardView } from "./dashboard-view"

const kyGet = vi.hoisted(() => vi.fn())

vi.mock("ky", () => ({ default: { get: kyGet } }))

const filters = { country: "all", sourceId: "", keyword: "", period: "7d" } as const
const readyState: DashboardState = {
  kind: "ready",
  data: DashboardBundleSchema.parse(FASTAPI_DASHBOARD_BUNDLE_FIXTURE),
}

afterEach(cleanup)
afterEach(() => kyGet.mockReset())

function loaderResponse(state: DashboardState) {
  return { status: 200, json: async () => state }
}

describe("truthful dashboard states", () => {
  it("renders a pending loading state without unavailable or blocked claims", () => {
    render(<DashboardView activeView="overview" filters={filters} state={{ kind: "loading" }} />)

    expect(screen.getByText("데이터를 불러오는 중입니다.")).toBeInTheDocument()
    expect(screen.queryByText("데이터 연결 확인 필요")).not.toBeInTheDocument()
    expect(screen.queryByText("차단됨")).not.toBeInTheDocument()
  })

  it("renders current FastAPI metrics and a semantic sentiment chart", () => {
    render(<DashboardView activeView="overview" filters={filters} state={readyState} />)

    expect(screen.getByText("12", { selector: ".metric-value" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "감성 구성" })).toBeInTheDocument()
    expect(screen.getByRole("table", { name: "감성 구성 원시 데이터" })).toBeInTheDocument()
    expect(
      screen.getByText("반영 범위 10/12", { selector: ".trend-panel .metric-note" }),
    ).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "언급량 추세" })).toBeInTheDocument()
    expect(
      screen.getByRole("img", { name: "언급량 추세: 현재 기간, 비교 기간" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("table", { name: "mention-trend-title 원시 데이터" }),
    ).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "소스 비교" })).toBeInTheDocument()
    expect(screen.getByRole("table", { name: "소스 상태 비교" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "소스 정렬" })).toBeInTheDocument()
  })

  it("renders post sentiment and known engagement from the posts endpoint", () => {
    render(<DashboardView activeView="posts" filters={filters} state={readyState} />)

    expect(screen.getByText("긍정")).toBeInTheDocument()
    expect(screen.getByText("댓글 7")).toBeInTheDocument()
    expect(screen.getByText("점수 15")).toBeInTheDocument()
    expect(screen.getByText("최신 원문").closest("p")).toHaveTextContent("2026. 07. 22.")
    expect(screen.getByLabelText("소스별 최근 수집")).toHaveTextContent("Reddit Prediction Markets")
  })

  it("preserves every filter in previous and next page links", () => {
    const pagedState: DashboardState = {
      kind: "ready",
      data: DashboardBundleSchema.parse({
        ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE,
        posts: {
          ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE.posts,
          page: { page: 2, page_size: 50, total_items: 151, has_next: true },
        },
      }),
    }
    render(
      <DashboardView
        activeView="posts"
        filters={{
          country: "kr",
          sourceId: "22222222-2222-4222-8222-222222222222",
          keyword: "monetary-policy",
          search: "금리 인하",
          period: "90d",
          page: 2,
        }}
        state={pagedState}
      />,
    )

    expect(screen.getByText("2/4 페이지")).toBeInTheDocument()
    for (const name of ["이전 페이지", "다음 페이지"]) {
      const href = screen.getByRole("link", { name }).getAttribute("href")
      expect(href).toContain("country=kr")
      expect(href).toContain("source_id=22222222-2222-4222-8222-222222222222")
      expect(href).toContain("keyword=monetary-policy")
      expect(href).toContain("search=%EA%B8%88%EB%A6%AC+%EC%9D%B8%ED%95%98")
      expect(href).toContain("period=90d")
    }
  })

  it("disables pagination boundaries without misleading links", () => {
    render(
      <DashboardView activeView="posts" filters={{ ...filters, page: 1 }} state={readyState} />,
    )

    expect(screen.getByText("1/1 페이지")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "이전 페이지" })).not.toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "다음 페이지" })).not.toBeInTheDocument()
    expect(screen.getByText("이전 페이지")).toHaveAttribute("aria-disabled", "true")
    expect(screen.getByText("다음 페이지")).toHaveAttribute("aria-disabled", "true")
  })

  it.each([
    {
      name: "literal search miss",
      filters: { ...filters, search: "일치하지 않는 검색" },
      state: {
        kind: "ready",
        data: {
          ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE,
          posts: {
            items: [],
            page: { page: 1, page_size: 50, total_items: 0, has_next: false },
          },
        },
      },
      copy: "검색어와 일치 없음",
    },
    {
      name: "empty period",
      filters: { ...filters, period: "30d" },
      state: {
        kind: "ready",
        data: {
          ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE,
          posts: {
            items: [],
            page: { page: 1, page_size: 50, total_items: 0, has_next: false },
          },
        },
      },
      copy: "선택 기간에 새 원문 없음",
    },
    {
      name: "API unavailable",
      filters,
      state: {
        kind: "unavailable",
        reason: "BFF에 연결할 수 없습니다.",
        correlationId: null,
        retryable: true,
      },
      copy: "수집/연결 상태 확인 필요",
    },
  ] as const)(
    "$name shows the exact truthful posts state",
    ({ filters: stateFilters, state, copy }) => {
      render(
        <DashboardView activeView="posts" filters={stateFilters} state={state as DashboardState} />,
      )

      expect(screen.getByText(copy)).toBeInTheDocument()
    },
  )

  it("passes a live mention-chart retry callback to the reusable frame", () => {
    const errorState: DashboardState = {
      kind: "ready",
      data: DashboardBundleSchema.parse({
        ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE,
        dashboard: {
          ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE.dashboard,
          mentions: { ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE.dashboard.mentions, status: "error" },
        },
      }),
    }
    const retry = vi.fn()
    render(
      <DashboardView activeView="overview" filters={filters} onRetry={retry} state={errorState} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }))
    expect(retry).toHaveBeenCalledOnce()
  })

  it("retries a retryable loader failure and returns focus to main content", async () => {
    kyGet
      .mockResolvedValueOnce(
        loaderResponse({
          kind: "unavailable",
          reason: "BFF에 연결할 수 없습니다.",
          correlationId: null,
          retryable: true,
        }),
      )
      .mockResolvedValueOnce(loaderResponse(readyState))

    render(<DashboardLoader activeView="overview" filters={filters} />)
    await waitFor(() => expect(kyGet).toHaveBeenCalledTimes(1))
    expect(kyGet.mock.calls[0]?.[1]).toMatchObject({ timeout: 30_000 })
    const retry = await screen.findByRole("button", { name: "다시 시도" })
    fireEvent.click(retry)

    expect(screen.getByText("데이터를 불러오는 중입니다.")).toBeInTheDocument()
    await waitFor(() => expect(kyGet).toHaveBeenCalledTimes(2))
    expect(
      await screen.findByRole("img", { name: "언급량 추세: 현재 기간, 비교 기간" }),
    ).toBeInTheDocument()
    await waitFor(() => expect(document.activeElement).toHaveAttribute("id", "main-content"))
  })

  it("renders rising keywords and source coverage from the reports endpoint", () => {
    render(<DashboardView activeView="reports" filters={filters} state={readyState} />)

    expect(screen.getByText("rate cuts")).toBeInTheDocument()
    expect(
      screen.getByText("Reddit Prediction Markets", { selector: ".source-coverage-list strong" }),
    ).toBeInTheDocument()
    expect(screen.getByText("10/12", { selector: ".source-coverage-list b" })).toBeInTheDocument()
    const reportPanel = screen
      .getByRole("heading", { name: "일일 보고서", level: 2 })
      .closest(".panel")
    expect(reportPanel).not.toBeNull()
    if (reportPanel instanceof HTMLElement) {
      expect(within(reportPanel).getByText("일부 완료")).toBeInTheDocument()
      expect(within(reportPanel).getByText(/독립 재현 근거/)).toBeInTheDocument()
      expect(within(reportPanel).queryByText("완료")).not.toBeInTheDocument()
    }
  })

  it("shows success only when a complete report has verified reproduction evidence", () => {
    const verifiedState: DashboardState = {
      kind: "ready",
      data: DashboardBundleSchema.parse({
        ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE,
        reports: {
          ...FASTAPI_DASHBOARD_BUNDLE_FIXTURE.reports,
          items: FASTAPI_DASHBOARD_BUNDLE_FIXTURE.reports.items.map((report) => ({
            ...report,
            reproduction_status: "verified",
          })),
        },
      }),
    }
    render(<DashboardView activeView="reports" filters={filters} state={verifiedState} />)

    const reportPanel = screen
      .getByRole("heading", { name: "일일 보고서", level: 2 })
      .closest(".panel")
    expect(reportPanel).not.toBeNull()
    if (reportPanel instanceof HTMLElement) {
      expect(within(reportPanel).getByText("완료")).toBeInTheDocument()
      expect(within(reportPanel).getByText("독립 재현 근거 검증 완료")).toBeInTheDocument()
    }
  })

  it.each(["overview", "posts", "reports", "status"] as const)(
    "targets real focusable evidence anchors on the %s page",
    (activeView) => {
      render(<DashboardView activeView={activeView} filters={filters} state={readyState} />)

      for (const link of screen.getAllByRole("link", { name: "상세 근거" })) {
        const href = link.getAttribute("href")
        expect(href).toMatch(/^#[a-z-]+$/)
        if (href !== null) {
          const target = document.querySelector(href)
          expect(target).not.toBeNull()
          expect(target).toHaveAttribute("tabindex", "-1")
        }
      }
    },
  )

  it("offers retry only for an enabled source whose last collection errored", () => {
    render(<DashboardView activeView="status" filters={filters} state={readyState} />)

    expect(screen.getByText("collection_timeout")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "검토된 한국 커뮤니티 수집 재시도" })).toBeEnabled()
  })

  it("keeps unavailable values unknown instead of fabricating zero or blocked evidence", () => {
    render(
      <DashboardView
        activeView="overview"
        filters={filters}
        state={{
          kind: "unavailable",
          reason: "대시보드 서비스에 연결할 수 없습니다.",
          correlationId: null,
          retryable: true,
        }}
      />,
    )

    expect(screen.getAllByText("집계되지 않음", { selector: ".metric-unknown" })).toHaveLength(4)
    expect(screen.queryByText("0건")).not.toBeInTheDocument()
    expect(screen.queryByText("차단됨")).not.toBeInTheDocument()
  })
})

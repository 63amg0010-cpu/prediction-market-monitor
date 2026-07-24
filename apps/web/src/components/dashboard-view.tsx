import { Clock, FunnelSimple, WarningOctagon } from "@phosphor-icons/react/ssr"

import type { DashboardState } from "../lib/dashboard-contract"
import { dashboardMetrics } from "../lib/dashboard-presenter"
import type { DashboardFilters } from "../lib/filter-contract"
import { formatTimestamp } from "../lib/time-format"
import { AppShell, type DashboardViewName } from "./app-shell"
import { DailyReport } from "./daily-report"
import { EvidenceRail } from "./evidence-rail"
import { FilterBar } from "./filter-bar"
import { MentionAnalysis } from "./mention-analysis"
import { MetricTile } from "./metric-tile"
import { OperationsPanel } from "./operations-panel"
import { PostsList } from "./posts-list"
import { SentimentChart } from "./sentiment-chart"

type DashboardViewProps = {
  readonly activeView: DashboardViewName
  readonly filters: DashboardFilters
  readonly onRetry?: () => void
  readonly state: DashboardState
}

const VIEW_COPY = {
  overview: ["커뮤니티 반응 개요", "지표보다 최신성과 반영 범위를 먼저 확인합니다."],
  posts: ["최근 게시글", "수치의 근거가 된 원문 링크를 확인합니다."],
  reports: ["일일 보고서", "수정 이력과 소스 반영 범위를 함께 확인합니다."],
  status: ["운영 상태", "수집, 분석, 게시 지연과 복구 가능성을 확인합니다."],
} as const

const VIEW_PATH = {
  overview: "/",
  posts: "/posts",
  reports: "/reports",
  status: "/status",
} as const

export function DashboardView({ activeView, filters, onRetry, state }: DashboardViewProps) {
  const data = state.kind === "ready" ? state.data : null
  const dashboard = data?.dashboard ?? null
  const unavailableReason =
    state.kind === "unavailable" || state.kind === "invalid_request" ? state.reason : null
  const metrics = dashboardMetrics(dashboard)
  const [title, description] = VIEW_COPY[activeView]
  const resultCount =
    data === null
      ? null
      : activeView === "reports"
        ? data.reports.page.total_items
        : activeView === "status"
          ? data.dashboard.sources.length
          : data.posts.page.total_items

  return (
    <AppShell activeView={activeView}>
      <div className="page-content">
        <header className="page-header">
          <div>
            <p className="eyebrow">PREDICTION MARKET MONITOR</p>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          <div className="as-of">
            <span>기준 시각</span>
            <strong>
              {dashboard === null
                ? "확인할 수 없음"
                : formatTimestamp(dashboard.generated_at, dashboard.generated_at)}
            </strong>
          </div>
        </header>
        <EvidenceRail activeView={activeView} data={data} unavailableReason={unavailableReason} />
        <FilterBar
          actionPath={VIEW_PATH[activeView]}
          filters={filters}
          resultCount={resultCount}
          sources={dashboard?.sources ?? []}
        />
        {state.kind === "loading" && (
          <output className="feedback-block feedback-pending">
            <Clock aria-hidden size={20} />
            <span>
              <strong>데이터를 불러오는 중입니다.</strong>
              <span className="feedback-message">현재 값은 아직 집계되지 않았습니다.</span>
            </span>
          </output>
        )}
        {state.kind === "invalid_request" && (
          <output className="feedback-block feedback-error">
            <FunnelSimple aria-hidden size={20} />
            <span>
              <strong>필터 입력 확인 필요</strong>
              <span className="feedback-message">{state.reason}</span>
            </span>
          </output>
        )}
        {state.kind === "unavailable" && (
          <output className="feedback-block">
            <WarningOctagon aria-hidden size={20} />
            <span>
              <strong>데이터 연결 확인 필요</strong>
              <span className="feedback-message">{state.reason}</span>
              {state.retryable && onRetry !== undefined && (
                <button className="button button-secondary" onClick={onRetry} type="button">
                  다시 시도
                </button>
              )}
            </span>
          </output>
        )}
        {activeView === "overview" && (
          <>
            <section aria-labelledby="metrics-title" className="metrics-section">
              <div className="section-heading">
                <h2 id="metrics-title">핵심 지표</h2>
                <span>선택 기간 {filters.period}</span>
              </div>
              <div className="metric-grid">
                <MetricTile label="언급량" metric={metrics.mentions} />
                <MetricTile label="기간 대비 증감률" metric={metrics.delta} />
                <MetricTile label="긍정 비율" metric={metrics.sentiment} />
                <MetricTile label="댓글 참여" metric={metrics.engagement} />
              </div>
            </section>
            <div className="dashboard-grid">
              <MentionAnalysis dashboard={dashboard} onRetry={onRetry} />
              <SentimentChart dashboard={dashboard} />
              <OperationsPanel data={data} />
              <PostsList data={data} />
              <DailyReport data={data} />
            </div>
          </>
        )}
        {activeView === "posts" && <PostsList data={data} />}
        {activeView === "reports" && <DailyReport data={data} />}
        {activeView === "status" && <OperationsPanel data={data} />}
      </div>
    </AppShell>
  )
}

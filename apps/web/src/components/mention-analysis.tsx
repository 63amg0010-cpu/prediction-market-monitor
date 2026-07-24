import type { DashboardResponse } from "../lib/dashboard-contract"
import { ChartFrame } from "./chart-frame"
import { Panel } from "./panel"
import { SourceStatusTable } from "./source-status-table"

type MentionAnalysisProps = {
  readonly dashboard: DashboardResponse | null
  readonly onRetry?: (() => void) | undefined
}

export function MentionAnalysis({ dashboard, onRetry }: MentionAnalysisProps) {
  const reference = dashboard?.generated_at ?? new Date(0).toISOString()
  return (
    <>
      <MentionTrendChart dashboard={dashboard} onRetry={onRetry} />
      <Panel className="source-comparison-panel" labelledBy="source-comparison-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">SOURCE COVERAGE</p>
            <h2 id="source-comparison-title">소스 비교</h2>
          </div>
          <span>{dashboard === null ? "집계 전" : `${dashboard.sources.length}개`}</span>
        </div>
        {dashboard === null || dashboard.sources.length === 0 ? (
          <div className="empty-state">
            <strong>
              {dashboard === null ? "소스 근거를 불러올 수 없습니다." : "등록된 소스가 없습니다."}
            </strong>
            <p>소스별 언급량이 없는 경우 임의 비율을 만들지 않습니다.</p>
          </div>
        ) : (
          <SourceStatusTable reference={reference} sources={dashboard.sources} />
        )}
        <p className="chart-caveat">
          API가 소스별 언급량을 제공하지 않아 상태와 최근 성공 근거만 비교합니다.
        </p>
      </Panel>
    </>
  )
}

function MentionTrendChart({ dashboard, onRetry }: MentionAnalysisProps) {
  const props = {
    className: "mention-trend-panel",
    description: "현재 기간과 비교 기간의 API 합계만 표시합니다.",
    id: "mention-trend-title",
    kind: "bar" as const,
    title: "언급량 추세",
  }
  if (dashboard === null) return <ChartFrame {...props} state="empty" />
  const { mentions } = dashboard
  const labels = ["현재 기간", "비교 기간"] as const
  const values = [mentions.current_count, mentions.previous_count] as const
  switch (mentions.status) {
    case "success":
      return <ChartFrame {...props} labels={labels} state="ready" values={values} />
    case "partial":
      return <ChartFrame {...props} labels={labels} state="partial" values={values} />
    case "pending":
      return <ChartFrame {...props} state="loading" />
    case "blocked":
      return <ChartFrame {...props} state="blocked" />
    case "error":
      return <ChartFrame {...props} onRetry={onRetry} state="error" />
    case "unknown":
      return <ChartFrame {...props} state="empty" />
  }
}

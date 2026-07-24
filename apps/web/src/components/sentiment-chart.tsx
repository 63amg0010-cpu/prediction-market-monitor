import type { DashboardResponse } from "../lib/dashboard-contract"
import { Panel } from "./panel"
import { StatusBadge } from "./status-badge"

type SentimentChartProps = { readonly dashboard: DashboardResponse | null }

const SENTIMENT_ROWS = [
  ["positive_count", "긍정", "sentiment-positive"],
  ["neutral_count", "중립", "sentiment-neutral"],
  ["negative_count", "부정", "sentiment-negative"],
] as const

function unavailableCopy(dashboard: DashboardResponse | null): string | null {
  if (dashboard === null) return "감성 데이터를 불러올 수 없습니다."
  switch (dashboard.analysis.status) {
    case "success":
    case "partial":
      return null
    case "pending":
      return "감성 분석을 기다리고 있습니다."
    case "blocked":
      return "감성 분석이 capability 검증에서 차단되었습니다."
    case "error":
      return "감성 분석 처리 중 오류가 발생했습니다."
    case "unknown":
      return "감성 분석 상태를 확인할 수 없습니다."
  }
}

export function SentimentChart({ dashboard }: SentimentChartProps) {
  const message = unavailableCopy(dashboard)
  const total =
    dashboard === null
      ? 0
      : dashboard.analysis.positive_count +
        dashboard.analysis.neutral_count +
        dashboard.analysis.negative_count
  return (
    <Panel className="trend-panel" labelledBy="sentiment-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ANALYSIS COMPOSITION</p>
          <h2 id="sentiment-title">감성 구성</h2>
        </div>
        <StatusBadge outcome={dashboard?.analysis.status ?? "unknown"} />
      </div>
      {message !== null ? (
        <div className="chart-empty">
          <strong>{message}</strong>
          <p>확인되지 않은 값을 0으로 그리지 않습니다.</p>
        </div>
      ) : total === 0 ? (
        <div className="empty-state">
          <strong>조건에 맞는 감성 결과가 없습니다.</strong>
          <p>분석은 완료됐지만 표시할 유효 감성 행이 없습니다.</p>
        </div>
      ) : (
        <>
          <div
            aria-label={`긍정 ${dashboard?.analysis.positive_count ?? 0}, 중립 ${dashboard?.analysis.neutral_count ?? 0}, 부정 ${dashboard?.analysis.negative_count ?? 0}`}
            className="sentiment-stack"
            role="img"
          >
            {SENTIMENT_ROWS.map(([key, label, className]) => (
              <span
                className={className}
                key={key}
                style={{ flexGrow: dashboard?.analysis[key] ?? 0 }}
                title={`${label} ${dashboard?.analysis[key] ?? 0}`}
              />
            ))}
          </div>
          <p className="metric-note">
            반영 범위 {dashboard?.analysis.valid_count}/{dashboard?.analysis.candidate_count}
          </p>
          <table aria-label="감성 구성 원시 데이터" className="chart-table">
            <thead>
              <tr>
                <th scope="col">감성</th>
                <th scope="col">건수</th>
              </tr>
            </thead>
            <tbody>
              {SENTIMENT_ROWS.map(([key, label]) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td>{dashboard?.analysis[key]}</td>
                </tr>
              ))}
              <tr>
                <th scope="row">감성 미확인</th>
                <td>{dashboard?.analysis.unknown_sentiment_count}</td>
              </tr>
            </tbody>
          </table>
        </>
      )}
      <p className="chart-caveat">시계열 투영은 현재 API에 없어 실제 감성 구성만 표시합니다.</p>
    </Panel>
  )
}

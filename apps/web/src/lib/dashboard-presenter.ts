import type { DashboardResponse, Metric, Outcome } from "./dashboard-contract"

function coverage(numerator: number, denominator: number) {
  return denominator > 0 ? { numerator, denominator } : null
}

function statusMetric(status: Outcome, reason: string): Metric | null {
  switch (status) {
    case "pending":
      return { kind: "pending", reason }
    case "blocked":
      return { kind: "blocked", reason }
    case "error":
      return { kind: "error", reason }
    case "unknown":
      return { kind: "unknown", reason }
    case "partial":
    case "success":
      return null
  }
}

export function dashboardMetrics(dashboard: DashboardResponse | null): {
  readonly mentions: Metric
  readonly delta: Metric
  readonly sentiment: Metric
  readonly engagement: Metric
} {
  if (dashboard === null) {
    return {
      mentions: { kind: "unknown", reason: "언급량 API 응답이 없습니다." },
      delta: { kind: "unknown", reason: "비교 기간이 집계되지 않았습니다." },
      sentiment: { kind: "unknown", reason: "분석 결과가 없습니다." },
      engagement: { kind: "unknown", reason: "참여도 집계가 제공되지 않았습니다." },
    }
  }

  const sentimentTotal =
    dashboard.analysis.positive_count +
    dashboard.analysis.neutral_count +
    dashboard.analysis.negative_count
  const engagementTotal =
    dashboard.engagement.comments_known_count + dashboard.engagement.comments_unknown_count

  return {
    mentions: (() => {
      const state = statusMetric(
        dashboard.mentions.status,
        `언급량 상태: ${dashboard.mentions.status}`,
      )
      if (state !== null) return state
      if (dashboard.mentions.status === "partial") {
        return {
          kind: "partial" as const,
          value: dashboard.mentions.current_count,
          unit: "건",
          coverage: null,
          reason: "일부 수집 구간만 반영되었습니다.",
        }
      }
      return {
        kind: "available" as const,
        value: dashboard.mentions.current_count,
        unit: "건",
        coverage: null,
      }
    })(),
    delta: (() => {
      const state = statusMetric(dashboard.mentions.status, "비교 기간 비율을 확인할 수 없습니다.")
      if (state !== null) return state
      if (dashboard.mentions.delta_rate === null) {
        return { kind: "null" as const, reason: "비교 기간 비율이 null로 제공되었습니다." }
      }
      const value = Number(dashboard.mentions.delta_rate) * 100
      return dashboard.mentions.status === "partial"
        ? {
            kind: "partial" as const,
            value,
            unit: "%",
            coverage: null,
            reason: "비교 기간 일부만 반영되었습니다.",
          }
        : { kind: "available" as const, value, unit: "%", coverage: null }
    })(),
    sentiment: (() => {
      const state = statusMetric(
        dashboard.analysis.status,
        "유효한 감성 분석을 확인할 수 없습니다.",
      )
      if (state !== null) return state
      if (sentimentTotal === 0) {
        return { kind: "null" as const, reason: "유효한 감성 합계가 null에 해당합니다." }
      }
      const value = (dashboard.analysis.positive_count / sentimentTotal) * 100
      const metricCoverage = coverage(
        dashboard.analysis.valid_count,
        dashboard.analysis.candidate_count,
      )
      return dashboard.analysis.status === "partial"
        ? {
            kind: "partial" as const,
            value,
            unit: "% 긍정",
            coverage: metricCoverage,
            reason: "일부 감성 결과만 반영되었습니다.",
          }
        : { kind: "available" as const, value, unit: "% 긍정", coverage: metricCoverage }
    })(),
    engagement: (() => {
      const state = statusMetric(dashboard.engagement.status, "댓글 합계를 확인할 수 없습니다.")
      if (state !== null) return state
      if (dashboard.engagement.comments_sum === null) {
        return { kind: "null" as const, reason: "댓글 합계가 null로 제공되었습니다." }
      }
      const metricCoverage = coverage(dashboard.engagement.comments_known_count, engagementTotal)
      return dashboard.engagement.status === "partial"
        ? {
            kind: "partial" as const,
            value: dashboard.engagement.comments_sum,
            unit: "댓글",
            coverage: metricCoverage,
            reason: "일부 댓글만 반영되었습니다.",
          }
        : {
            kind: "available" as const,
            value: dashboard.engagement.comments_sum,
            unit: "댓글",
            coverage: metricCoverage,
          }
    })(),
  }
}

export function aggregateOutcomes(outcomes: readonly Outcome[]): Outcome {
  if (outcomes.length === 0) return "unknown"
  if (outcomes.every((outcome) => outcome === "success")) return "success"
  if (outcomes.every((outcome) => outcome === "pending")) return "pending"
  if (outcomes.every((outcome) => outcome === "blocked")) return "blocked"
  if (outcomes.every((outcome) => outcome === "error")) return "error"
  if (outcomes.every((outcome) => outcome === "unknown")) return "unknown"
  return "partial"
}

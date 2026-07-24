import type { Metric } from "../lib/dashboard-contract"
import { StatusBadge } from "./status-badge"

type MetricTileProps = {
  readonly label: string
  readonly metric: Metric
}

const NUMBER_FORMATTER = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 })

function statusOutcome(metric: Metric): "pending" | "error" | "blocked" | "partial" | "unknown" {
  switch (metric.kind) {
    case "pending":
    case "loading":
      return "pending"
    case "error":
      return "error"
    case "blocked":
      return "blocked"
    case "partial":
      return "partial"
    case "null":
    case "unknown":
      return "unknown"
    case "available":
      return "unknown"
  }
}

function stateLabel(metric: Exclude<Metric, { readonly kind: "available" | "partial" }>): string {
  switch (metric.kind) {
    case "loading":
      return "불러오는 중"
    case "pending":
      return "집계 대기 중"
    case "error":
      return "집계 오류"
    case "blocked":
      return "집계 차단됨"
    case "null":
      return "값 없음"
    case "unknown":
      return "집계되지 않음"
  }
}

export function MetricTile({ label, metric }: MetricTileProps) {
  return (
    <article className="metric-tile">
      <h3>{label}</h3>
      {metric.kind === "available" || metric.kind === "partial" ? (
        <>
          {metric.kind === "partial" && <StatusBadge outcome="partial" />}
          <p className="metric-value">
            {NUMBER_FORMATTER.format(metric.value)} <span>{metric.unit}</span>
          </p>
          <p className="metric-note">
            {metric.coverage === null
              ? "전체 범위"
              : `반영 범위 ${metric.coverage.numerator}/${metric.coverage.denominator}`}
          </p>
          {metric.kind === "partial" && <p className="metric-note">{metric.reason}</p>}
        </>
      ) : (
        <>
          <StatusBadge outcome={statusOutcome(metric)} />
          <p className={`metric-state metric-${metric.kind}`}>{stateLabel(metric)}</p>
          <p className="metric-note">{metric.reason}</p>
        </>
      )}
    </article>
  )
}

import type { Metric } from "../lib/dashboard-contract"
import { MetricTile } from "./metric-tile"

const METRIC_SCENARIOS: readonly { readonly label: string; readonly metric: Metric }[] = [
  { label: "정상 값", metric: { kind: "available", value: 12, unit: "검증값", coverage: null } },
  {
    label: "큰 수·긴 단위",
    metric: {
      kind: "available",
      value: 123456789,
      unit: "검증된 게시글 및 댓글 합계 건수",
      coverage: null,
    },
  },
  {
    label: "부분 값",
    metric: {
      kind: "partial",
      value: 10,
      unit: "검증값",
      coverage: { numerator: 10, denominator: 12 },
      reason: "일부 수집 구간만 반영되었습니다.",
    },
  },
  { label: "null 값", metric: { kind: "null", reason: "API가 null을 반환했습니다." } },
  { label: "unknown 값", metric: { kind: "unknown", reason: "상태를 판별할 수 없습니다." } },
  { label: "로딩 값", metric: { kind: "loading", reason: "값을 불러오는 중입니다." } },
  { label: "대기 값", metric: { kind: "pending", reason: "분석 큐가 처리되면 값이 채워집니다." } },
  { label: "오류 값", metric: { kind: "error", reason: "원인과 재시도 가능 여부를 표시합니다." } },
  {
    label: "차단 값",
    metric: { kind: "blocked", reason: "필요 capability가 확인되지 않았습니다." },
  },
]

export function ShowcaseMetrics() {
  return (
    <section className="metric-grid" aria-label="지표 상태">
      {METRIC_SCENARIOS.map(({ label, metric }) => (
        <MetricTile key={label} label={label} metric={metric} />
      ))}
    </section>
  )
}

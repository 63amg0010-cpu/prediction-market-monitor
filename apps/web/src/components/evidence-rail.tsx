import Link from "next/link"

import type { DashboardData, Outcome } from "../lib/dashboard-contract"
import { aggregateOutcomes } from "../lib/dashboard-presenter"
import { reportOutcome } from "../lib/report-verification"
import { formatTimestamp, freshnessAt, freshnessLabel } from "../lib/time-format"
import type { DashboardViewName } from "./app-shell"
import { StatusBadge } from "./status-badge"

type EvidenceRailProps = {
  readonly activeView: DashboardViewName
  readonly data: DashboardData | null
  readonly unavailableReason: string | null
  readonly id?: string
  readonly previewState?: "normal" | "null" | "unknown" | "loading" | "error" | "blocked"
}

export type EvidenceRailPreviewState = NonNullable<EvidenceRailProps["previewState"]>

const PREVIEW_TARGETS = [
  "#showcase-table-title",
  "#showcase-chart-line",
  "#showcase-chart-stacked-partial",
  "#posts-title",
] as const
const PREVIEW_REFERENCE = "2026-07-22T12:00:00.000Z"

const EVIDENCE_TARGETS: Readonly<
  Record<DashboardViewName, readonly [string, string, string, string]>
> = {
  overview: ["#source-title", "#source-title", "#queue-title", "#report-title"],
  posts: ["#posts-title", "#posts-title", "#posts-title", "#posts-title"],
  reports: ["#report-title", "#report-title", "#report-title", "#report-title"],
  status: ["#source-title", "#source-title", "#queue-title", "#freshness-title"],
}

type EvidenceItem = {
  readonly label: string
  readonly outcome: Outcome
  readonly detail: string
  readonly observedAt: string | null
  readonly coverage: { readonly numerator: number; readonly denominator: number } | null
  readonly href: string
}

function coverageText(coverage: EvidenceItem["coverage"]): string {
  return coverage === null
    ? "반영 범위 확인 불가"
    : `반영 범위 ${coverage.numerator}/${coverage.denominator}`
}

function evidenceItems(
  activeView: DashboardViewName,
  data: DashboardData | null,
  reason: string | null,
): readonly EvidenceItem[] {
  const targets = EVIDENCE_TARGETS[activeView]
  if (data === null) {
    const detail = reason ?? "근거 데이터가 제공되지 않았습니다."
    return [
      { label: "전체 수집", href: targets[0] },
      { label: "소스 반영", href: targets[1] },
      { label: "AI 분석", href: targets[2] },
      { label: "일일 보고서", href: targets[3] },
    ].map((item) => ({
      ...item,
      outcome: "unknown" as const,
      detail,
      observedAt: null,
      coverage: null,
    }))
  }

  const dashboard = data.dashboard
  const activeSources = dashboard.sources.filter((source) => source.enabled)
  const successfulSources = activeSources.filter((source) => source.status === "success")
  const report = data.reports.items.at(0)
  return [
    {
      label: "전체 수집",
      outcome: dashboard.operations.collection_status,
      detail: `활성 소스 ${successfulSources.length}/${activeSources.length}개 최근 수집 성공`,
      observedAt: dashboard.operations.last_complete_collection_at,
      coverage: { numerator: successfulSources.length, denominator: activeSources.length },
      href: targets[0],
    },
    {
      label: "소스 반영",
      outcome: aggregateOutcomes(activeSources.map((source) => source.status)),
      detail: "활성 소스별 최신 성공과 오류를 함께 반영합니다.",
      observedAt: dashboard.operations.last_complete_collection_at,
      coverage: { numerator: successfulSources.length, denominator: activeSources.length },
      href: targets[1],
    },
    {
      label: "AI 분석",
      outcome: dashboard.operations.analysis_status,
      detail: `대기 ${dashboard.analysis.pending_count}건 · 차단 ${dashboard.analysis.blocked_count}건`,
      observedAt: dashboard.operations.last_analysis_at,
      coverage: {
        numerator: dashboard.analysis.valid_count,
        denominator: dashboard.analysis.candidate_count,
      },
      href: targets[2],
    },
    {
      label: "일일 보고서",
      outcome: reportOutcome(report),
      detail:
        report === undefined
          ? "확인 가능한 보고서가 없습니다."
          : `${report.report_date_seoul} revision ${report.revision} · ${report.reproduction_status === "verified" ? "독립 재현 근거 검증 완료" : "독립 재현 근거 미검증"}`,
      observedAt: report?.created_at ?? null,
      coverage:
        report === undefined
          ? null
          : { numerator: report.relevant_count, denominator: report.candidate_count },
      href: targets[3],
    },
  ]
}

function previewItems(state: EvidenceRailPreviewState): readonly EvidenceItem[] {
  const labels = ["전체 수집", "소스 반영", "AI 분석", "일일 보고서"] as const
  const detail =
    state === "normal"
      ? "검증 가능한 상태 예시입니다. 제품 데이터로 해석하지 않습니다."
      : state === "loading"
        ? "근거 상태를 불러오는 중입니다."
        : state === "error"
          ? "근거 요청이 오류로 끝났습니다."
          : state === "blocked"
            ? "필요 capability가 없어 근거를 확인할 수 없습니다."
            : state === "null"
              ? "근거 데이터가 연결되지 않았습니다."
              : "근거 상태를 아직 판별하지 않았습니다."
  return labels.map((label, index) => ({
    label,
    outcome:
      state === "normal"
        ? index === 2
          ? "partial"
          : "success"
        : state === "loading"
          ? "pending"
          : state === "error"
            ? "error"
            : state === "blocked"
              ? "blocked"
              : "unknown",
    detail,
    observedAt:
      state === "normal"
        ? index === 0
          ? "2026-07-22T11:30:00.000Z"
          : index === 1
            ? "2026-07-22T08:00:00.000Z"
            : index === 3
              ? "2026-07-22T11:55:00.000Z"
              : null
        : null,
    coverage: state === "normal" ? { numerator: index === 2 ? 3 : 4, denominator: 4 } : null,
    href: PREVIEW_TARGETS[index] ?? "#showcase-table-title",
  }))
}

export function EvidenceRail({
  activeView,
  data,
  unavailableReason,
  id,
  previewState,
}: EvidenceRailProps) {
  const items =
    previewState === undefined
      ? evidenceItems(activeView, data, unavailableReason)
      : previewItems(previewState)
  const reference =
    previewState === undefined
      ? (data?.dashboard.generated_at ?? new Date(0).toISOString())
      : PREVIEW_REFERENCE
  const titleId = id === undefined ? "evidence-title" : `${id}-title`
  const summary =
    previewState === "normal"
      ? "검증 상태 예시"
      : previewState === "loading"
        ? "근거 로딩 중"
        : previewState === "error"
          ? "근거 확인 오류"
          : previewState === "blocked"
            ? "근거 확인 차단"
            : previewState === "unknown"
              ? "상태 미확인"
              : data === null
                ? "근거 확인 불가"
                : `분석 ${coverageText(items[2]?.coverage ?? null)}`
  return (
    <section aria-labelledby={titleId} className="evidence-rail" data-preview-state={previewState}>
      <div className="section-heading">
        <div>
          <p className="eyebrow">DATA CONFIDENCE</p>
          <h2 id={titleId}>데이터 신뢰도</h2>
        </div>
        <span className="rail-summary">{summary}</span>
      </div>
      <details className="evidence-disclosure">
        <summary>데이터 신뢰도 상세 보기</summary>
        <div className="evidence-grid" aria-live="polite">
          {items.map((item) => {
            const freshness = freshnessAt(item.observedAt, reference)
            return (
              <article className="evidence-cell" key={item.label}>
                <div className="evidence-cell-heading">
                  <h3>{item.label}</h3>
                  <StatusBadge outcome={item.outcome} />
                </div>
                <p>{item.detail}</p>
                <strong>{coverageText(item.coverage)}</strong>
                <small>{formatTimestamp(item.observedAt, reference)}</small>
                <div className="evidence-actions">
                  <span className={`freshness-badge freshness-${freshness}`}>
                    {freshnessLabel(freshness)}
                  </span>
                  <Link href={item.href}>상세 근거</Link>
                </div>
              </article>
            )
          })}
        </div>
      </details>
    </section>
  )
}

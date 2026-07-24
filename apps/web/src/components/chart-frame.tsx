import type { ReactNode } from "react"

import { Panel } from "./panel"
import { StatusBadge } from "./status-badge"

export type ChartKind = "line" | "bar" | "stacked"
export type ChartState = "ready" | "loading" | "empty" | "error" | "blocked" | "partial"

type ChartFrameProps = {
  readonly id: string
  readonly title: string
  readonly kind: ChartKind
  readonly description?: ReactNode
  readonly className?: string
  readonly onRetry?: (() => void) | undefined
} & (
  | {
      readonly state: "ready" | "partial"
      readonly values: readonly number[]
      readonly labels: readonly string[]
    }
  | {
      readonly state: "loading" | "empty" | "error" | "blocked"
      readonly values?: never
      readonly labels?: never
    }
) & { readonly retryHref?: string }

const STATE_COPY: Record<ChartState, { readonly label: string; readonly detail: string }> = {
  ready: { label: "정상", detail: "검증 가능한 값만 표시합니다." },
  loading: { label: "대기", detail: "차트 데이터를 불러오는 중입니다." },
  empty: { label: "빈 결과", detail: "조건에 맞는 값이 없습니다." },
  error: { label: "오류", detail: "차트 데이터를 확인할 수 없습니다." },
  blocked: { label: "차단", detail: "필요 capability가 없어 차트를 표시할 수 없습니다." },
  partial: { label: "부분 완료", detail: "일부 구간만 확인된 상태입니다." },
}

function outcomeFor(
  state: ChartState,
): "success" | "pending" | "unknown" | "error" | "blocked" | "partial" {
  switch (state) {
    case "ready":
      return "success"
    case "loading":
      return "pending"
    case "empty":
      return "unknown"
    case "error":
      return "error"
    case "blocked":
      return "blocked"
    case "partial":
      return "partial"
  }
}

function normalizeValues(values: readonly number[]): readonly number[] {
  const maximum = Math.max(...values, 1)
  return values.map((value) => Math.max(0, Math.min(100, (value / maximum) * 100)))
}

function renderLine(values: readonly number[], labels: readonly string[], title: string) {
  const normalized = normalizeValues(values)
  const points = normalized
    .map((value, index) => {
      const x = normalized.length <= 1 ? 50 : (index / (normalized.length - 1)) * 100
      const y = 36 - (value / 100) * 30
      return `${x},${y}`
    })
    .join(" ")
  return (
    <svg
      aria-label={`${title}: ${labels.join(", ")}`}
      className="chart-frame-line"
      role="img"
      viewBox="0 0 100 40"
    >
      <polyline className="chart-line-path" points={points} />
    </svg>
  )
}

function renderBar(values: readonly number[], labels: readonly string[], title: string) {
  return (
    <div aria-label={`${title}: ${labels.join(", ")}`} className="chart-frame-bar" role="img">
      {normalizeValues(values).map((value, index) => (
        <span
          className="chart-bar-value"
          key={`${labels[index] ?? "bar"}-${index.toString()}`}
          style={{ blockSize: `${Math.max(value, 4)}%` }}
          title={`${labels[index] ?? `항목 ${index + 1}`} ${Math.round(value)}`}
        />
      ))}
    </div>
  )
}

function renderStacked(values: readonly number[], labels: readonly string[], title: string) {
  return (
    <div aria-label={`${title}: ${labels.join(", ")}`} className="chart-frame-stacked" role="img">
      {values.map((value, index) => (
        <span
          className={`chart-stacked-value chart-stacked-value-${index + 1}`}
          key={`${labels[index] ?? "stack"}-${index.toString()}`}
          style={{ flexGrow: Math.max(value, 0) }}
          title={`${labels[index] ?? `항목 ${index + 1}`} ${value}`}
        />
      ))}
    </div>
  )
}

function renderChart(
  kind: ChartKind,
  values: readonly number[],
  labels: readonly string[],
  title: string,
) {
  switch (kind) {
    case "line":
      return renderLine(values, labels, title)
    case "bar":
      return renderBar(values, labels, title)
    case "stacked":
      return renderStacked(values, labels, title)
  }
}

function renderSemanticTable(id: string, labels: readonly string[], values: readonly number[]) {
  return (
    <details className="chart-frame-data">
      <summary>표 데이터 보기</summary>
      <table>
        <caption className="sr-only">{id} 원시 데이터</caption>
        <thead>
          <tr>
            <th scope="col">구간</th>
            <th scope="col">값</th>
          </tr>
        </thead>
        <tbody>
          {values.map((value, index) => (
            <tr key={`${labels[index] ?? "값"}-${index.toString()}`}>
              <th scope="row">{labels[index] ?? "구간"}</th>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

export function ChartFrame({
  id,
  title,
  kind,
  state,
  description,
  className,
  values,
  labels,
  onRetry,
  retryHref,
}: ChartFrameProps) {
  const stateCopy = STATE_COPY[state]
  const chart =
    (state === "ready" || state === "partial") && values !== undefined && labels !== undefined
      ? renderChart(kind, values, labels, title)
      : null
  return (
    <Panel
      className={`chart-frame-panel chart-frame-panel-${kind} ${className ?? ""}`.trim()}
      labelledBy={id}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">REUSABLE CHART FRAME</p>
          <h3 id={id}>{title}</h3>
        </div>
        <StatusBadge outcome={outcomeFor(state)} />
      </div>
      {description !== undefined && <p className="chart-frame-description">{description}</p>}
      {state === "loading" ? (
        <output aria-busy="true" className="chart-frame-state">
          <span className="chart-frame-skeleton" />
          <strong>{stateCopy.detail}</strong>
        </output>
      ) : state === "empty" || state === "error" || state === "blocked" ? (
        <output className={`chart-frame-state chart-frame-state-${state}`}>
          <strong>{stateCopy.label}</strong>
          <p>{stateCopy.detail}</p>
          {(state === "error" || state === "blocked") &&
            (onRetry === undefined ? (
              retryHref !== undefined && (
                <a className="button button-secondary" href={retryHref}>
                  다시 시도
                </a>
              )
            ) : (
              <button className="button button-secondary" onClick={onRetry} type="button">
                다시 시도
              </button>
            ))}
        </output>
      ) : (
        <>
          <div className={`chart-frame-visual chart-frame-visual-${state}`}>{chart}</div>
          {labels !== undefined && values !== undefined && renderSemanticTable(id, labels, values)}
        </>
      )}
      <p className="chart-caveat">{stateCopy.detail}</p>
    </Panel>
  )
}

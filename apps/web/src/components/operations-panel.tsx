import type { DashboardData } from "../lib/dashboard-contract"
import { formatTimestamp } from "../lib/time-format"
import { Panel } from "./panel"
import { RetryCollectionButton } from "./retry-collection-button"
import { StatusBadge } from "./status-badge"

type OperationsPanelProps = { readonly data: DashboardData | null }

export function OperationsPanel({ data }: OperationsPanelProps) {
  const dashboard = data?.dashboard
  const reference = dashboard?.generated_at ?? new Date(0).toISOString()
  return (
    <div className="operations-stack">
      <Panel labelledBy="queue-title">
        <div className="section-heading">
          <h2 id="queue-title" tabIndex={-1}>
            분석 대기열
          </h2>
          <StatusBadge outcome={dashboard?.operations.analysis_status ?? "unknown"} />
        </div>
        <dl className="compact-facts">
          <div>
            <dt>대기</dt>
            <dd>{dashboard?.operations.pending_analysis_count ?? "확인 불가"}</dd>
          </div>
          <div>
            <dt>차단</dt>
            <dd>{dashboard?.operations.blocked_analysis_count ?? "확인 불가"}</dd>
          </div>
          <div>
            <dt>마지막 분석</dt>
            <dd>{formatTimestamp(dashboard?.operations.last_analysis_at ?? null, reference)}</dd>
          </div>
        </dl>
        <p>
          {dashboard === undefined
            ? "분석 상태 근거를 불러올 수 없습니다."
            : `유효 ${dashboard.analysis.valid_count}/${dashboard.analysis.candidate_count}건`}
        </p>
      </Panel>
      <Panel labelledBy="freshness-title">
        <div className="section-heading">
          <h2 id="freshness-title" tabIndex={-1}>
            운영 기준 시각
          </h2>
        </div>
        <dl className="freshness-grid">
          <div>
            <dt>마지막 완료 수집</dt>
            <dd>
              {formatTimestamp(
                dashboard?.operations.last_complete_collection_at ?? null,
                reference,
              )}
            </dd>
          </div>
          <div>
            <dt>마지막 분석</dt>
            <dd>{formatTimestamp(dashboard?.operations.last_analysis_at ?? null, reference)}</dd>
          </div>
          <div>
            <dt>응답 생성</dt>
            <dd>{dashboard === undefined ? "확인 불가" : formatTimestamp(reference, reference)}</dd>
          </div>
        </dl>
        <p className="metric-note">API가 제공하지 않는 스케줄 지연값은 계산해 만들지 않습니다.</p>
      </Panel>
      <Panel labelledBy="source-title">
        <div className="section-heading">
          <h2 id="source-title" tabIndex={-1}>
            소스 상태와 복구
          </h2>
        </div>
        {dashboard === undefined || dashboard.sources.length === 0 ? (
          <div className="empty-state">
            <strong>
              {dashboard === undefined
                ? "소스 상태를 불러올 수 없습니다."
                : "등록된 소스가 없습니다."}
            </strong>
            <p>확인되지 않은 상태를 오류나 차단으로 표시하지 않습니다.</p>
          </div>
        ) : (
          <ul className="source-list" id="source-status">
            {dashboard.sources.map((source) => (
              <li key={source.source_id}>
                <div className="source-main">
                  <strong>{source.display_name}</strong>
                  <small>
                    {source.country.toUpperCase()} ·{" "}
                    {formatTimestamp(source.latest_successful_run_at, reference)}
                  </small>
                  <span>
                    게시 순서 {source.visible_publication_sequence ?? "미확인"} · 오류 코드{" "}
                    <code>{source.failure_code ?? "없음"}</code>
                  </span>
                </div>
                <StatusBadge outcome={source.status} />
                {source.retry_eligible && (
                  <RetryCollectionButton
                    sourceId={source.source_id}
                    sourceName={source.display_name}
                  />
                )}
                {!source.retry_eligible && source.retry_block_reason !== null && (
                  <small className="retry-block-reason">
                    재시도 불가: {source.retry_block_reason}
                  </small>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}

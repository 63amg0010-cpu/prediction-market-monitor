import type { DashboardData } from "../lib/dashboard-contract"
import { reportOutcome } from "../lib/report-verification"
import { formatTimestamp } from "../lib/time-format"
import { Panel } from "./panel"
import { StatusBadge } from "./status-badge"

type DailyReportProps = { readonly data: DashboardData | null }

export function DailyReport({ data }: DailyReportProps) {
  const report = data?.reports.items.at(0)
  const reference = data?.dashboard.generated_at ?? report?.created_at ?? new Date(0).toISOString()

  return (
    <Panel className="report-panel" labelledBy="report-title" variant="raised">
      <div className="section-heading">
        <div>
          <p className="eyebrow">DAILY LEDGER</p>
          <h2 id="report-title" tabIndex={-1}>
            일일 보고서
          </h2>
        </div>
        {report !== undefined && <StatusBadge outcome={reportOutcome(report)} />}
      </div>
      {report === undefined ? (
        <div className="empty-state">
          <strong>
            {data === null ? "보고서를 불러올 수 없습니다." : "조건에 맞는 보고서가 없습니다."}
          </strong>
          <p>입력 집합과 소스 반영 범위가 확인된 보고서만 표시합니다.</p>
        </div>
      ) : (
        <div className="report-content">
          <div className="report-meta">
            <p className="report-revision">
              {report.report_date_seoul} · revision {report.revision}
            </p>
            <small>{formatTimestamp(report.created_at, reference)}</small>
          </div>
          <p className="report-verification-note">
            {report.reproduction_status === "verified"
              ? "독립 재현 근거 검증 완료"
              : `${report.status === "complete" ? "메타데이터 완료" : "메타데이터 일부 완료"} · 독립 재현 근거가 검증되지 않았습니다.`}
          </p>
          <dl className="report-provenance">
            <div>
              <dt>매니페스트</dt>
              <dd>
                <code>{report.manifest_id}</code>
              </dd>
            </div>
            <div>
              <dt>입력 집합 해시</dt>
              <dd>
                <code>{report.input_set_hash}</code>
              </dd>
            </div>
            <div>
              <dt>매니페스트 해시</dt>
              <dd>
                <code>{report.manifest_payload_sha256}</code>
              </dd>
            </div>
            <div>
              <dt>보고서 해시</dt>
              <dd>
                <code>{report.report_payload_sha256}</code>
              </dd>
            </div>
          </dl>
          <dl className="compact-facts report-facts">
            <div>
              <dt>분석 반영</dt>
              <dd>
                {report.relevant_count}/{report.candidate_count}
              </dd>
            </div>
            <div>
              <dt>대기</dt>
              <dd>{report.pending_count}</dd>
            </div>
            <div>
              <dt>댓글 합계</dt>
              <dd>{report.comments_sum ?? "미확인"}</dd>
            </div>
            <div>
              <dt>점수 합계</dt>
              <dd>{report.score_sum ?? "미확인"}</dd>
            </div>
          </dl>
          <section aria-labelledby="report-highlight-title">
            <h3 id="report-highlight-title">주요 반응</h3>
            {report.highlights.length === 0 ? (
              <p>확인된 주요 반응이 없습니다.</p>
            ) : (
              <ul className="report-list">
                {report.highlights.map((item) => (
                  <li key={item.category}>
                    <strong>{item.category}</strong>
                    <span>
                      {item.primary_count}건 · 증감 {item.delta > 0 ? "+" : ""}
                      {item.delta}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="report-keyword-title">
            <h3 id="report-keyword-title">상승 키워드</h3>
            {report.rising_keywords.length === 0 ? (
              <p>상승 기준을 충족한 키워드가 없습니다.</p>
            ) : (
              <ul className="keyword-list">
                {report.rising_keywords.map((item) => (
                  <li key={item.phrase}>
                    <strong>{item.phrase}</strong>
                    <span>
                      {item.primary_count}건 · 비교 {item.comparison_count}건 · 증감{" "}
                      {item.delta > 0 ? "+" : ""}
                      {item.delta}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="report-source-title">
            <h3 id="report-source-title">소스 반영 범위</h3>
            <ul className="source-coverage-list">
              {report.source_coverage.map((source) => (
                <li key={`${source.country}-${source.community}`}>
                  <span>
                    <strong>{source.community}</strong>
                    <small>{source.collection_status}</small>
                  </span>
                  <b>
                    {source.coverage_numerator}/{source.coverage_denominator}
                  </b>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </Panel>
  )
}

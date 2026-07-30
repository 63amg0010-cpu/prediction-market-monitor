import { ArrowSquareOut } from "@phosphor-icons/react/ssr"
import Link from "next/link"

import type { DashboardData, DashboardState, Outcome, PostItem } from "../lib/dashboard-contract"
import type { DashboardFilters } from "../lib/filter-contract"
import { paginationUrls } from "../lib/filter-contract"
import { formatTimestamp } from "../lib/time-format"
import { Panel } from "./panel"
import { StatusBadge } from "./status-badge"

type PostsListProps = {
  readonly actionPath: string
  readonly data: DashboardData | null
  readonly filters: DashboardFilters
  readonly stateKind: DashboardState["kind"]
}

function analysisOutcome(post: PostItem): Outcome {
  switch (post.analysis_state) {
    case "valid":
      return "success"
    case "pending":
      return "pending"
    case "blocked_capability":
      return "blocked"
    case "failed_retryable":
    case "failed_terminal":
    case "invalid_output":
      return "error"
  }
}

function sentimentLabel(sentiment: PostItem["sentiment"]): string {
  switch (sentiment) {
    case "positive":
      return "긍정"
    case "neutral":
      return "중립"
    case "negative":
      return "부정"
    case null:
      return "감성 미확인"
  }
}

function emptyState(
  stateKind: DashboardState["kind"],
  filters: DashboardFilters,
): { readonly title: string; readonly detail: string } {
  if (stateKind === "loading") {
    return {
      title: "게시글을 불러오는 중입니다.",
      detail: "현재 결과를 0건으로 확정하지 않습니다.",
    }
  }
  if (stateKind !== "ready") {
    return {
      title: "수집/연결 상태 확인 필요",
      detail: "연결 상태를 확인한 뒤 다시 시도해 주세요.",
    }
  }
  if ((filters.search ?? "").length > 0) {
    return {
      title: "검색어와 일치 없음",
      detail: "글 검색어를 바꾸거나 기간과 소스 범위를 넓혀 보세요.",
    }
  }
  return {
    title: "선택 기간에 새 원문 없음",
    detail: "기간이나 소스 범위를 넓혀 다시 확인해 보세요.",
  }
}

export function PostsList({ actionPath, data, filters, stateKind }: PostsListProps) {
  const posts = data?.posts.items
  const reference = data?.dashboard.generated_at ?? new Date(0).toISOString()
  const page = data?.posts.page
  const totalPages =
    page === undefined ? 1 : Math.max(1, Math.ceil(page.total_items / page.page_size))
  const currentPage = page === undefined ? 1 : Math.min(page.page, totalPages)
  const pagination = paginationUrls(actionPath, { ...filters, page: currentPage }, totalPages)
  const newestPublication =
    posts === undefined || posts.length === 0
      ? null
      : posts.reduce((newest, post) =>
          Date.parse(post.published_at) > Date.parse(newest.published_at) ? post : newest,
        ).published_at
  const empty = emptyState(stateKind, filters)
  return (
    <Panel className="posts-panel" labelledBy="posts-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">EVIDENCE</p>
          <h2 id="posts-title" tabIndex={-1}>
            최근 게시글
          </h2>
        </div>
        <span>{posts === undefined ? "집계 전" : `${page?.total_items ?? posts.length}건`}</span>
      </div>
      <div className="posts-evidence-summary">
        <p>
          <strong>최신 원문</strong>
          <span>{formatTimestamp(newestPublication, reference)}</span>
        </p>
        <ul aria-label="소스별 최근 수집" className="posts-source-freshness">
          {(data?.dashboard.sources ?? []).map((source) => (
            <li key={source.source_id}>
              <span>{source.display_name}</span>
              <StatusBadge outcome={source.status} />
              <small>{formatTimestamp(source.latest_successful_run_at, reference)}</small>
            </li>
          ))}
          {data !== null && data.dashboard.sources.length === 0 && <li>소스 상태 없음</li>}
          {data === null && <li>소스 상태 확인 불가</li>}
        </ul>
      </div>
      {posts === undefined || posts.length === 0 ? (
        <div className="empty-state">
          <strong>{empty.title}</strong>
          <p>{empty.detail}</p>
        </div>
      ) : (
        <ul className="post-list">
          {posts.map((post) => (
            <li key={post.id}>
              <div className="post-main">
                <span className="post-meta">
                  {post.country.toUpperCase()} · {post.source_name} ·{" "}
                  {formatTimestamp(post.published_at, reference)}
                </span>
                <strong lang={post.country === "us" ? "en" : "ko"}>{post.title}</strong>
                <div className="post-facts">
                  <StatusBadge outcome={analysisOutcome(post)} />
                  <span className={`sentiment-label sentiment-${post.sentiment ?? "unknown"}`}>
                    {sentimentLabel(post.sentiment)}
                  </span>
                  <span>
                    {post.comments_count === null || post.comments_count === undefined
                      ? "댓글 미확인"
                      : `댓글 ${post.comments_count}`}
                  </span>
                  <span>
                    {post.score === null || post.score === undefined
                      ? "점수 미확인"
                      : `점수 ${post.score}`}
                  </span>
                </div>
              </div>
              <a href={post.original_url} rel="noreferrer" target="_blank">
                원문 열기 <span className="sr-only">(새 창)</span>
                <ArrowSquareOut aria-hidden size={16} />
              </a>
            </li>
          ))}
        </ul>
      )}
      {page !== undefined && (
        <nav aria-label="게시글 페이지 이동" className="pagination">
          {pagination.previous === null ? (
            <span aria-disabled="true" className="button button-ghost pagination-disabled">
              이전 페이지
            </span>
          ) : (
            <Link className="button button-secondary" href={pagination.previous}>
              이전 페이지
            </Link>
          )}
          <strong aria-live="polite">
            {currentPage}/{totalPages} 페이지
          </strong>
          {pagination.next === null ? (
            <span aria-disabled="true" className="button button-ghost pagination-disabled">
              다음 페이지
            </span>
          ) : (
            <Link className="button button-secondary" href={pagination.next}>
              다음 페이지
            </Link>
          )}
        </nav>
      )}
    </Panel>
  )
}

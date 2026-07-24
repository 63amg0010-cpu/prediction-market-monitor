import { ArrowSquareOut } from "@phosphor-icons/react/ssr"

import type { DashboardData, Outcome, PostItem } from "../lib/dashboard-contract"
import { formatTimestamp } from "../lib/time-format"
import { Panel } from "./panel"
import { StatusBadge } from "./status-badge"

type PostsListProps = { readonly data: DashboardData | null }

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

export function PostsList({ data }: PostsListProps) {
  const posts = data?.posts.items
  const reference = data?.dashboard.generated_at ?? new Date(0).toISOString()
  return (
    <Panel className="posts-panel" labelledBy="posts-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">EVIDENCE</p>
          <h2 id="posts-title" tabIndex={-1}>
            최근 게시글
          </h2>
        </div>
        <span>
          {posts === undefined ? "집계 전" : `${data?.posts.page.total_items ?? posts.length}건`}
        </span>
      </div>
      {posts === undefined || posts.length === 0 ? (
        <div className="empty-state">
          <strong>
            {posts === undefined
              ? "게시글을 불러올 수 없습니다."
              : "조건에 맞는 게시글이 없습니다."}
          </strong>
          <p>
            {posts === undefined
              ? "원문을 확인하기 전에는 게시글 수를 0으로 확정하지 않습니다."
              : "필터 범위에서 확인된 원문이 없습니다."}
          </p>
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
    </Panel>
  )
}

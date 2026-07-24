import type { Outcome } from "../lib/dashboard-contract"
import { StatusBadge } from "./status-badge"

export type PostRowState = "ready" | "pending" | "error" | "unknown"
export type PostRowSentiment = "positive" | "neutral" | "negative"

type PostRowProps = {
  readonly title: string
  readonly source: string
  readonly state: PostRowState
  readonly comments?: number | null
  readonly publishedAt?: string | null
  readonly sentiment?: PostRowSentiment | null
  readonly href?: string
}

const OUTCOME_BY_STATE: Record<PostRowState, Outcome> = {
  ready: "success",
  pending: "pending",
  error: "error",
  unknown: "unknown",
}

const LABEL_BY_STATE: Record<PostRowState, string> = {
  ready: "분석 가능",
  pending: "분석 대기",
  error: "분석 오류",
  unknown: "분석 미확인",
}

const LABEL_BY_SENTIMENT: Record<PostRowSentiment, string> = {
  positive: "긍정",
  neutral: "중립",
  negative: "부정",
}

export function PostRow({
  title,
  source,
  state,
  comments = null,
  publishedAt = null,
  sentiment = null,
  href,
}: PostRowProps) {
  return (
    <li className={`post-row post-row-${state}`} data-state={state}>
      <div className="post-main">
        <span className="post-meta">{source}</span>
        <strong>{title}</strong>
        <div className="post-facts">
          <StatusBadge outcome={OUTCOME_BY_STATE[state]} />
          <span>{LABEL_BY_STATE[state]}</span>
          <span>{publishedAt === null ? "게시 시각 미확인" : `게시 ${publishedAt}`}</span>
          <span>
            {sentiment === null ? "감성 미확인" : `감성 ${LABEL_BY_SENTIMENT[sentiment]}`}
          </span>
          <span>{comments === null ? "댓글 미확인" : `댓글 ${comments}`}</span>
        </div>
      </div>
      {href === undefined ? (
        <span className="post-row-link post-row-link-disabled">원문 미확인</span>
      ) : (
        <a href={href} rel="noreferrer" target="_blank">
          원문 보기
        </a>
      )}
    </li>
  )
}

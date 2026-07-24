import {
  CheckCircle,
  CircleHalf,
  Clock,
  LockKey,
  Question,
  WarningOctagon,
} from "@phosphor-icons/react/ssr"

import type { Outcome } from "../lib/dashboard-contract"

type StatusBadgeProps = {
  readonly outcome: Outcome
}

export function StatusBadge({ outcome }: StatusBadgeProps) {
  switch (outcome) {
    case "success":
      return (
        <span className="status-badge status-success">
          <CheckCircle aria-hidden size={16} />
          완료
        </span>
      )
    case "pending":
      return (
        <span className="status-badge status-pending">
          <Clock aria-hidden size={16} />
          대기 중
        </span>
      )
    case "blocked":
      return (
        <span className="status-badge status-blocked">
          <LockKey aria-hidden size={16} />
          차단됨
        </span>
      )
    case "partial":
      return (
        <span className="status-badge status-partial">
          <CircleHalf aria-hidden size={16} />
          일부 완료
        </span>
      )
    case "error":
      return (
        <span className="status-badge status-error">
          <WarningOctagon aria-hidden size={16} />
          오류
        </span>
      )
    case "unknown":
      return (
        <span className="status-badge status-unknown">
          <Question aria-hidden size={16} />
          집계되지 않음
        </span>
      )
  }
}

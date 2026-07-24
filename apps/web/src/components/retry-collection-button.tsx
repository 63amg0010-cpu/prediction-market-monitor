"use client"

import { ArrowClockwise } from "@phosphor-icons/react"
import ky from "ky"
import { useState } from "react"

import { CollectionRetryResponseSchema } from "../lib/command-contract"

type RetryCollectionButtonProps = {
  readonly sourceId: string
  readonly sourceName: string
}

export function RetryCollectionButton({ sourceId, sourceName }: RetryCollectionButtonProps) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function retry(): Promise<void> {
    setBusy(true)
    setMessage(null)
    try {
      const response = await ky.post("/api/commands/collection-retry", {
        retry: 0,
        throwHttpErrors: false,
        json: { requestId: crypto.randomUUID(), sourceId },
      })
      let raw: unknown
      try {
        raw = await response.json()
      } catch (error) {
        if (!(error instanceof Error)) throw error
        raw = null
      }
      const parsed = CollectionRetryResponseSchema.safeParse(raw)
      if (response.ok && parsed.success) {
        setMessage(parsed.data.created ? "재시도를 접수했습니다." : "이미 접수된 재시도입니다.")
      } else {
        setMessage("재시도를 접수하지 못했습니다. 로그인과 오류 근거를 확인해 주세요.")
      }
    } catch (error) {
      if (!(error instanceof Error)) throw error
      setMessage("재시도 서비스에 연결할 수 없습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="retry-control">
      <button
        aria-busy={busy}
        aria-label={`${sourceName} 수집 재시도`}
        className="button button-secondary"
        disabled={busy}
        onClick={() => void retry()}
        type="button"
      >
        <ArrowClockwise aria-hidden size={18} />
        {busy ? "접수 중" : "수집 재시도"}
      </button>
      {message !== null && <small aria-live="polite">{message}</small>}
    </span>
  )
}

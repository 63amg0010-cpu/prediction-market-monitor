"use client"

import { X } from "@phosphor-icons/react"
import { type KeyboardEvent, useEffect, useRef, useState } from "react"

import { ChartFrame } from "./chart-frame"

type Overlay = "command" | "dialog" | null
type ValidationRetryState = "error" | "loading" | "ready"

const VALIDATION_RETRY_DELAY_MS = 160

export function ShowcaseInteractions() {
  const [overlay, setOverlay] = useState<Overlay>(null)
  const [retryRequestCount, setRetryRequestCount] = useState(0)
  const [retryState, setRetryState] = useState<ValidationRetryState>("error")
  const commandTrigger = useRef<HTMLButtonElement>(null)
  const dialogTrigger = useRef<HTMLButtonElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (overlay !== null) closeButton.current?.focus()
  }, [overlay])

  useEffect(() => {
    if (retryState !== "loading") return
    const timer = window.setTimeout(() => setRetryState("ready"), VALIDATION_RETRY_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [retryState])

  function close(): void {
    const trigger = overlay === "command" ? commandTrigger.current : dialogTrigger.current
    setOverlay(null)
    queueMicrotask(() => trigger?.focus())
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === "Escape") {
      event.preventDefault()
      close()
      return
    }
    if (event.key !== "Tab") return
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )
    if (focusable === undefined || focusable.length === 0) return
    const activeIndex = Array.from(focusable).findIndex(
      (element) => element === document.activeElement,
    )
    const nextIndex = event.shiftKey ? activeIndex - 1 : activeIndex + 1
    const wrappedIndex = (nextIndex + focusable.length) % focusable.length
    event.preventDefault()
    focusable.item(wrappedIndex).focus()
  }

  function retryValidation(): void {
    setRetryRequestCount((current) => current + 1)
    setRetryState("loading")
    document.getElementById("main-content")?.focus()
  }

  return (
    <>
      <div className="showcase-row">
        <button
          className="button button-secondary"
          onClick={() => setOverlay("command")}
          ref={commandTrigger}
          type="button"
        >
          명령 팔레트 열기
        </button>
        <button
          className="button button-secondary"
          onClick={() => setOverlay("dialog")}
          ref={dialogTrigger}
          type="button"
        >
          검증 대화상자 열기
        </button>
      </div>
      <section
        aria-label="재시도 상태 검증"
        data-validation-retry-request-count={retryRequestCount}
        data-validation-retry-state={retryState}
      >
        <p className="chart-caveat">
          제품 데이터를 흉내 내지 않는 검증 전용 상태 전이: 오류 → 로딩 → 준비됨.
        </p>
        {retryState === "error" && (
          <ChartFrame
            description="검증용 오류 상태입니다. 재시도하면 로딩 상태로 전환됩니다."
            id="showcase-validation-retry"
            kind="line"
            onRetry={retryValidation}
            state="error"
            title="재시도 검증 · 오류"
          />
        )}
        {retryState === "loading" && (
          <ChartFrame
            description="검증용 요청이 진행 중입니다."
            id="showcase-validation-retry"
            kind="line"
            state="loading"
            title="재시도 검증 · 로딩"
          />
        )}
        {retryState === "ready" && (
          <ChartFrame
            description="검증 전용 상태 전이가 준비됨으로 끝났습니다. 제품 데이터가 아닙니다."
            id="showcase-validation-retry"
            kind="line"
            labels={["검증 시작", "검증 완료"]}
            state="ready"
            title="재시도 검증 · 준비됨"
            values={[1, 1]}
          />
        )}
      </section>
      {overlay !== null && (
        <div className="showcase-overlay">
          <section
            aria-label={overlay === "command" ? "검증 명령 팔레트" : "검증 대화상자"}
            aria-modal="true"
            className="showcase-modal"
            onKeyDown={handleDialogKeyDown}
            ref={dialogRef}
            role="dialog"
          >
            <div className="section-heading">
              <strong>{overlay === "command" ? "빠른 이동" : "실행 전 확인"}</strong>
              <button
                aria-label="닫기"
                className="icon-button"
                onClick={close}
                ref={closeButton}
                type="button"
              >
                <X aria-hidden size={20} />
              </button>
            </div>
            {overlay === "command" ? (
              <label>
                명령 검색
                <input placeholder="페이지 또는 동작 검색" />
              </label>
            ) : (
              <p>영향 범위와 취소 동작을 확인한 뒤 실행합니다.</p>
            )}
            <button className="button button-primary" onClick={close} type="button">
              확인
            </button>
          </section>
        </div>
      )}
    </>
  )
}

"use client"

import { Funnel, X } from "@phosphor-icons/react"
import Link from "next/link"
import { type KeyboardEvent, useEffect, useRef, useState } from "react"

import type { DashboardFilters } from "../lib/filter-contract"

type FilterSource = {
  readonly source_id: string
  readonly display_name: string
}

type FilterBarProps = {
  readonly actionPath: string
  readonly filters: DashboardFilters
  readonly resultCount: number | null
  readonly sources: readonly FilterSource[]
}

const PERIOD_LABELS: Readonly<Record<DashboardFilters["period"], string>> = {
  "24h": "24시간",
  "7d": "7일",
  "14d": "14일",
  "30d": "30일",
  "90d": "90일",
}

function appliedFilterLabels(
  filters: DashboardFilters,
  sources: readonly FilterSource[],
): readonly string[] {
  const labels: string[] = []
  if (filters.country !== "all") labels.push(filters.country === "kr" ? "한국" : "미국")
  if (filters.sourceId.length > 0) {
    labels.push(
      sources.find((source) => source.source_id === filters.sourceId)?.display_name ??
        "소스 선택됨",
    )
  }
  if (filters.keyword.length > 0) labels.push(`“${filters.keyword}”`)
  if (filters.period !== "7d") labels.push(PERIOD_LABELS[filters.period])
  return labels
}

export function FilterBar({ actionPath, filters, resultCount, sources }: FilterBarProps) {
  const [open, setOpen] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const applied = appliedFilterLabels(filters, sources)

  useEffect(() => {
    if (open) formRef.current?.querySelector<HTMLElement>("select, input, button, a")?.focus()
  }, [open])

  function cancel(): void {
    setOpen(false)
    triggerRef.current?.focus()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLFormElement>): void {
    if (!open) return
    if (event.key === "Escape") {
      event.preventDefault()
      cancel()
      return
    }
    if (event.key !== "Tab") return
    const focusable = formRef.current?.querySelectorAll<HTMLElement>(
      "select, input, button:not([disabled]), a[href]",
    )
    if (focusable === undefined || focusable.length === 0) return
    const first = focusable.item(0)
    const last = focusable.item(focusable.length - 1)
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <section className="filter-disclosure" aria-label="대시보드 필터 영역">
      <div className="filter-trigger-row">
        <button
          aria-expanded={open}
          aria-controls="dashboard-filter-form"
          className="button button-secondary filter-trigger"
          onClick={() => setOpen(true)}
          ref={triggerRef}
          type="button"
        >
          <Funnel aria-hidden size={20} />
          필터 열기
        </button>
        <ul className="filter-chips" aria-label="적용 중인 필터">
          {applied.slice(0, 2).map((label) => (
            <li key={label}>{label}</li>
          ))}
          {applied.length > 2 && <li>외 {applied.length - 2}개</li>}
          {applied.length === 0 && <li>기본 조건</li>}
        </ul>
      </div>
      <form
        action={actionPath}
        aria-label="대시보드 필터"
        className="filter-bar filter-tablet-cluster"
        data-open={open}
        id="dashboard-filter-form"
        method="get"
        onKeyDown={handleKeyDown}
        ref={formRef}
        role={open ? "dialog" : undefined}
      >
        <div className="filter-sheet-heading">
          <div>
            <strong>필터</strong>
            <span>{resultCount === null ? "현재 결과 집계 전" : `현재 결과 ${resultCount}건`}</span>
          </div>
          <button aria-label="필터 닫기" className="icon-button" onClick={cancel} type="button">
            <X aria-hidden size={20} />
          </button>
        </div>
        <div className="filter-fields">
          <label>
            국가
            <select defaultValue={filters.country} name="country">
              <option value="all">전체</option>
              <option value="kr">한국</option>
              <option value="us">미국</option>
            </select>
          </label>
          <label>
            소스
            <select defaultValue={filters.sourceId} name="source_id">
              <option value="">전체 소스</option>
              {sources.map((source) => (
                <option key={source.source_id} value={source.source_id}>
                  {source.display_name}
                </option>
              ))}
              {filters.sourceId.length > 0 &&
                !sources.some((source) => source.source_id === filters.sourceId) && (
                  <option value={filters.sourceId}>선택한 소스</option>
                )}
            </select>
          </label>
          <label>
            키워드
            <input
              defaultValue={filters.keyword}
              maxLength={300}
              name="keyword"
              placeholder="예: 예측시장, 폴리마켓, 확률"
            />
          </label>
          <label>
            기간
            <select defaultValue={filters.period} name="period">
              <option value="24h">24시간</option>
              <option value="7d">7일</option>
              <option value="14d">14일</option>
              <option value="30d">30일</option>
              <option value="90d">90일</option>
            </select>
          </label>
        </div>
        <div className="filter-sheet-actions">
          <button className="button button-ghost filter-cancel" onClick={cancel} type="button">
            취소
          </button>
          <Link className="button button-ghost" href={actionPath}>
            초기화
          </Link>
          <button className="button button-primary" type="submit">
            적용
          </button>
        </div>
      </form>
    </section>
  )
}

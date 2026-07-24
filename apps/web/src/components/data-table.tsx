"use client"

import type { ReactNode } from "react"
import { useMemo, useState } from "react"

export type DataTableState = "ready" | "loading" | "empty" | "error" | "partial"

export type DataTableColumn<Row> = {
  readonly key: string
  readonly header: string
  readonly render: (row: Row) => ReactNode
  readonly sortable?: boolean
  readonly sortValue?: (row: Row) => string | number
}

type DataTableProps<Row extends { readonly id: string }> = {
  readonly caption: string
  readonly columns: readonly DataTableColumn<Row>[]
  readonly rows: readonly Row[]
  readonly state?: DataTableState
  readonly emptyMessage?: string
  readonly pageSize?: number
}

type SortDirection = "ascending" | "descending"

const STATE_MESSAGES: Record<Exclude<DataTableState, "ready" | "partial">, string> = {
  loading: "행을 불러오는 중입니다.",
  empty: "표시할 행이 없습니다.",
  error: "행 데이터를 확인할 수 없습니다.",
}

export function DataTable<Row extends { readonly id: string }>({
  caption,
  columns,
  rows,
  state = "ready",
  emptyMessage,
  pageSize = 10,
}: DataTableProps<Row>) {
  const [sort, setSort] = useState<{
    readonly key: string
    readonly direction: SortDirection
  } | null>(null)
  const [page, setPage] = useState(0)
  const isMessageState = state === "loading" || state === "empty" || state === "error"
  const sortedRows = useMemo(() => {
    if (sort === null) return rows
    const column = columns.find((candidate) => candidate.key === sort.key)
    if (column === undefined || column.sortable !== true) return rows
    const direction = sort.direction === "ascending" ? 1 : -1
    return rows
      .map((row, index) => ({ row, index, value: column.sortValue?.(row) ?? row.id }))
      .sort((left, right) => {
        const leftValue = left.value
        const rightValue = right.value
        const comparison =
          typeof leftValue === "number" && typeof rightValue === "number"
            ? leftValue - rightValue
            : String(leftValue).localeCompare(String(rightValue), "ko")
        return comparison === 0 ? left.index - right.index : comparison * direction
      })
      .map(({ row }) => row)
  }, [columns, rows, sort])
  const normalizedPageSize = Math.max(1, pageSize)
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / normalizedPageSize))
  const currentPage = Math.min(page, pageCount - 1)
  const visibleRows = sortedRows.slice(
    currentPage * normalizedPageSize,
    (currentPage + 1) * normalizedPageSize,
  )

  function toggleSort(column: DataTableColumn<Row>): void {
    if (column.sortable !== true) return
    setSort((current) => ({
      key: column.key,
      direction:
        current?.key === column.key && current.direction === "ascending"
          ? "descending"
          : "ascending",
    }))
    setPage(0)
  }

  return (
    <div
      aria-busy={state === "loading"}
      className={`data-table-frame data-table-state-${state}`}
      data-state={state}
    >
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                aria-sort={
                  column.sortable === true
                    ? sort?.key === column.key
                      ? sort.direction
                      : "none"
                    : undefined
                }
                key={column.key}
                scope="col"
              >
                {column.sortable === true ? (
                  <button
                    aria-label={`${column.header} 정렬`}
                    className="data-table-sort-button"
                    onClick={(event) => {
                      toggleSort(column)
                      event.currentTarget.focus()
                    }}
                    type="button"
                  >
                    {column.header}
                  </button>
                ) : (
                  column.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {isMessageState ? (
            <tr>
              <td colSpan={columns.length}>
                <output aria-live={state === "error" ? "assertive" : "polite"}>
                  {emptyMessage ?? STATE_MESSAGES[state]}
                </output>
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <output>{emptyMessage ?? STATE_MESSAGES.empty}</output>
              </td>
            </tr>
          ) : (
            visibleRows.map((row) => (
              <tr key={row.id}>
                {columns.map((column) => (
                  <td data-label={column.header} key={column.key}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {!isMessageState && pageCount > 1 && (
        <nav aria-label={`${caption} 페이지 탐색`} className="data-table-pagination">
          <button
            aria-label={`${caption} 이전 페이지`}
            className="button button-ghost"
            disabled={currentPage === 0}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
            type="button"
          >
            이전
          </button>
          <output aria-live="polite">
            {currentPage + 1} / {pageCount} · {visibleRows.length}행 / {sortedRows.length}행
          </output>
          <button
            aria-label={`${caption} 다음 페이지`}
            className="button button-ghost"
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
            type="button"
          >
            다음
          </button>
        </nav>
      )}
    </div>
  )
}

"use client"

import type { SourceStatus } from "../lib/dashboard-contract"
import { formatTimestamp } from "../lib/time-format"
import { DataTable, type DataTableColumn } from "./data-table"
import { StatusBadge } from "./status-badge"

type SourceStatusTableProps = {
  readonly reference: string
  readonly sources: readonly SourceStatus[]
}

const COLUMNS: readonly DataTableColumn<SourceTableRow>[] = [
  {
    key: "source",
    header: "소스",
    render: (source) => source.display_name,
    sortable: true,
    sortValue: (source) => source.display_name,
  },
  {
    key: "status",
    header: "상태",
    render: (source) => <StatusBadge outcome={source.status} />,
    sortable: true,
    sortValue: (source) => source.status,
  },
  {
    key: "latest",
    header: "최근 성공",
    render: (source) => formatTimestamp(source.latest_successful_run_at, source.reference),
    sortable: true,
    sortValue: (source) => source.latest_successful_run_at ?? "",
  },
]

type SourceTableRow = SourceStatus & { readonly id: string; readonly reference: string }

export function SourceStatusTable({ reference, sources }: SourceStatusTableProps) {
  const rows: readonly SourceTableRow[] = sources.map((source) => ({
    ...source,
    id: source.source_id,
    reference,
  }))
  return (
    <DataTable<SourceTableRow>
      caption="소스 상태 비교"
      columns={COLUMNS}
      rows={rows}
      state="ready"
    />
  )
}

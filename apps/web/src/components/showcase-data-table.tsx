"use client"

import { DataTable, type DataTableColumn } from "./data-table"
import { Panel } from "./panel"
import { PostRow } from "./post-row"
import { StatusBadge } from "./status-badge"

const OUTCOMES = ["success", "pending", "blocked", "partial", "error", "unknown"] as const
const VALIDATION_ROWS = Array.from({ length: 50 }, (_, index) => index + 1)

type ShowcaseRow = {
  readonly id: string
  readonly source: string
  readonly state: (typeof OUTCOMES)[number]
  readonly coverage: string
}

const TABLE_ROWS: readonly ShowcaseRow[] = [
  { id: "source-1", source: "소스 A", state: "success", coverage: "4/4" },
  { id: "source-2", source: "소스 B", state: "partial", coverage: "3/4" },
]

const DENSE_TABLE_ROWS: readonly ShowcaseRow[] = Array.from({ length: 50 }, (_, index) => ({
  id: `dense-source-${index + 1}`,
  source: `소스 ${String(index + 1).padStart(2, "0")}`,
  state: index % 5 === 0 ? "partial" : "success",
  coverage: index % 5 === 0 ? "3/4" : "4/4",
}))

const TABLE_COLUMNS: readonly DataTableColumn<ShowcaseRow>[] = [
  {
    key: "source",
    header: "소스",
    render: (row) => row.source,
    sortable: true,
    sortValue: (row) => row.source,
  },
  {
    key: "state",
    header: "상태",
    render: (row) => <StatusBadge outcome={row.state} />,
    sortable: true,
    sortValue: (row) => row.state,
  },
  {
    key: "coverage",
    header: "반영 범위",
    render: (row) => row.coverage,
    sortable: true,
    sortValue: (row) => row.coverage,
  },
]

export function ShowcaseDataTable() {
  return (
    <Panel labelledBy="showcase-table-title">
      <h2 id="showcase-table-title">DataTable과 PostRow 시나리오</h2>
      <p className="chart-caveat">
        정상·부분·대기·오류·미확인 행을 임의의 제품 데이터로 오인하지 않도록 상태를 명시합니다.
      </p>
      <div className="showcase-table-grid">
        <DataTable
          caption="소스 상태 한 행"
          columns={TABLE_COLUMNS}
          rows={TABLE_ROWS.slice(0, 1)}
          state="ready"
        />
        <DataTable
          caption="소스 상태 밀도 50행"
          columns={TABLE_COLUMNS}
          rows={DENSE_TABLE_ROWS}
          state="partial"
        />
        <DataTable caption="소스 상태 빈 결과" columns={TABLE_COLUMNS} rows={[]} state="empty" />
        <DataTable caption="소스 상태 대기" columns={TABLE_COLUMNS} rows={[]} state="loading" />
        <DataTable caption="소스 상태 오류" columns={TABLE_COLUMNS} rows={[]} state="error" />
      </div>
      <ul aria-label="게시글 행 상태 시나리오" className="showcase-post-rows">
        <PostRow
          comments={14}
          href="https://www.reddit.com/"
          publishedAt="2026-07-22 11:42 KST"
          sentiment="positive"
          source="검증 소스 A · KR"
          state="ready"
          title="정상 분석 행"
        />
        <PostRow
          comments={null}
          publishedAt="2026-07-22 10:18 KST"
          sentiment="neutral"
          source="검증 소스 B · KR"
          state="pending"
          title="대기 분석 행"
        />
        <PostRow
          comments={null}
          publishedAt={null}
          sentiment={null}
          source="검증 소스 C · KR"
          state="error"
          title="오류 분석 행"
        />
        <PostRow
          comments={null}
          publishedAt={null}
          sentiment={null}
          source="검증 소스 D · KR"
          state="unknown"
          title="미확인 분석 행"
        />
      </ul>
    </Panel>
  )
}

export function ShowcaseVolume() {
  return (
    <Panel labelledBy="posts-title">
      <h2 id="posts-title" tabIndex={-1}>
        데이터 볼륨과 행 상태
      </h2>
      <p className="chart-caveat">
        50개 검증 행을 10행 단위 페이지로 나눠 밀도와 줄바꿈을 확인합니다.
      </p>
      <ul className="showcase-data-rows showcase-volume-list">
        {VALIDATION_ROWS.map((row) => (
          <li aria-label={`검증 데이터 행 ${row}`} key={row}>
            <span>검증 소스 {row.toString().padStart(2, "0")} · KR</span>
            <StatusBadge outcome={OUTCOMES[(row - 1) % OUTCOMES.length] ?? "unknown"} />
            <button className="button button-secondary" type="button">
              행 동작
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

import "@testing-library/jest-dom/vitest"

import { fireEvent, render, screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { DataTable, type DataTableColumn } from "./data-table"

type Row = { readonly id: string; readonly name: string; readonly value: number }

const columns: readonly DataTableColumn<Row>[] = [
  {
    key: "name",
    header: "이름",
    render: (row) => row.name,
    sortable: true,
    sortValue: (row) => row.name,
  },
  {
    key: "value",
    header: "값",
    render: (row) => row.value,
    sortable: true,
    sortValue: (row) => row.value,
  },
]

describe("DataTable contracts", () => {
  it("exposes keyboard-focusable sortable headers with aria-sort", () => {
    render(
      <DataTable
        caption="정렬 표"
        columns={columns}
        rows={[
          { id: "b", name: "Beta", value: 2 },
          { id: "a", name: "Alpha", value: 1 },
        ]}
      />,
    )

    const sortButton = screen.getByRole("button", { name: "이름 정렬" })
    const header = screen.getByRole("columnheader", { name: "이름" })
    expect(sortButton).toBeEnabled()
    expect(sortButton).not.toHaveAttribute("aria-sort")
    expect(header).toHaveAttribute("aria-sort", "none")

    fireEvent.click(sortButton)
    expect(sortButton).toHaveFocus()
    expect(header).toHaveAttribute("aria-sort", "ascending")
    const table = screen.getByRole("table", { name: "정렬 표" })
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("Alpha")

    fireEvent.click(sortButton)
    expect(header).toHaveAttribute("aria-sort", "descending")
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("Beta")
  })

  it("paginates dense rows without hiding the row count", () => {
    const rows = Array.from({ length: 21 }, (_, index) => ({
      id: `row-${index + 1}`,
      name: `행 ${index + 1}`,
      value: index + 1,
    }))
    render(<DataTable caption="밀도 표" columns={columns} rows={rows} state="partial" />)

    expect(screen.getByRole("navigation", { name: "밀도 표 페이지 탐색" })).toBeInTheDocument()
    expect(screen.getByText("1 / 3 · 10행 / 21행")).toBeInTheDocument()
    const table = screen.getByRole("table", { name: "밀도 표" })
    expect(within(table).getAllByRole("row")).toHaveLength(11)

    fireEvent.click(screen.getByRole("button", { name: "밀도 표 다음 페이지" }))
    expect(screen.getByText("2 / 3 · 10행 / 21행")).toBeInTheDocument()
    expect(within(table).getAllByRole("row")).toHaveLength(11)
  })
})

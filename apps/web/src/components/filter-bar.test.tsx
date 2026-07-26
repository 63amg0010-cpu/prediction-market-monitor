import "@testing-library/jest-dom/vitest"

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { FilterBar } from "./filter-bar"

afterEach(cleanup)

describe("responsive filter disclosure", () => {
  it("returns focus to the trigger after cancelling the filter sheet", () => {
    render(
      <FilterBar
        actionPath="/"
        filters={{
          country: "kr",
          sourceId: "11111111-1111-4111-8111-111111111111",
          keyword: "rate cuts",
          period: "24h",
        }}
        resultCount={12}
        sources={[
          {
            source_id: "11111111-1111-4111-8111-111111111111",
            display_name: "Reddit Prediction Markets",
          },
        ]}
      />,
    )
    const trigger = screen.getByRole("button", { name: "필터 열기" })

    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("dialog", { name: "대시보드 필터" })).toBeInTheDocument()
    expect(screen.getByText("현재 결과 12건")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "취소" }))
    expect(trigger).toHaveAttribute("aria-expanded", "false")
    expect(trigger).toHaveFocus()
  })

  it("summarizes at most two applied filters and the remaining count", () => {
    render(
      <FilterBar
        actionPath="/"
        filters={{
          country: "kr",
          sourceId: "11111111-1111-4111-8111-111111111111",
          keyword: "rate cuts",
          period: "24h",
        }}
        resultCount={12}
        sources={[]}
      />,
    )

    expect(screen.getByText("외 2개")).toBeInTheDocument()
  })

  it("closes the mobile sheet with Escape and exposes the tablet wrapping cluster", () => {
    render(
      <FilterBar
        actionPath="/"
        filters={{ country: "all", sourceId: "", keyword: "", period: "7d" }}
        resultCount={null}
        sources={[]}
      />,
    )
    const trigger = screen.getByRole("button", { name: "필터 열기" })
    fireEvent.click(trigger)
    const dialog = screen.getByRole("dialog", { name: "대시보드 필터" })

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute("aria-expanded", "false")
    expect(dialog).toHaveClass("filter-tablet-cluster")
  })

  it("moves focus to the sheet close control when the disclosed sheet opens", () => {
    render(
      <FilterBar
        actionPath="/"
        filters={{ country: "all", sourceId: "", keyword: "", period: "7d" }}
        resultCount={null}
        sources={[]}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "필터 열기" }))

    expect(screen.getByRole("dialog", { name: "대시보드 필터" })).toHaveClass(
      "filter-tablet-cluster",
    )
    expect(screen.getByRole("button", { name: "필터 닫기" })).toHaveFocus()
  })

  it("offers reviewed keyword examples and the 90 day evidence window", () => {
    render(
      <FilterBar
        actionPath="/posts"
        filters={{ country: "all", sourceId: "", keyword: "", period: "90d" }}
        resultCount={20}
        sources={[]}
      />,
    )

    expect(screen.getByRole("textbox", { name: "키워드" })).toHaveAttribute(
      "placeholder",
      "예: 예측시장, 폴리마켓, 확률",
    )
    expect(screen.getByRole("option", { name: "90일" })).toHaveValue("90d")
    expect(screen.getByRole("list", { name: "적용 중인 필터" })).toHaveTextContent("90일")
  })
})

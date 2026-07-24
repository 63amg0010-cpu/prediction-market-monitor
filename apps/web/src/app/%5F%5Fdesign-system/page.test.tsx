import "@testing-library/jest-dom/vitest"

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import DesignSystemPage from "./page"

afterEach(cleanup)

describe("primitive showcase matrix", () => {
  it("renders the required evidence, chart, data-volume, dialog, and command states", () => {
    render(<DesignSystemPage />)

    expect(screen.getByRole("heading", { name: "데이터 신뢰도 매트릭스" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "언급량 추세" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "소스 비교" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "명령 팔레트 열기" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "검증 대화상자 열기" })).toBeInTheDocument()
    expect(screen.getAllByRole("listitem", { name: /검증 데이터 행/ })).toHaveLength(50)
  }, 15000)

  it("renders reusable viewport, evidence, metric, chart, table, and post states", () => {
    const { container } = render(<DesignSystemPage />)

    expect(container.querySelectorAll(".app-shell-preview")).toHaveLength(3)
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="normal"]')).toHaveLength(
      1,
    )
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="null"]')).toHaveLength(1)
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="unknown"]')).toHaveLength(
      1,
    )
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="loading"]')).toHaveLength(
      1,
    )
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="error"]')).toHaveLength(1)
    expect(container.querySelectorAll('.evidence-rail[data-preview-state="blocked"]')).toHaveLength(
      1,
    )
    for (const state of ["null", "unknown", "loading", "pending", "error", "blocked"]) {
      expect(container.querySelector(`.metric-${state}`)).toBeInTheDocument()
    }
    expect(container.querySelectorAll(".chart-frame-panel")).toHaveLength(8)
    expect(container.querySelector(".chart-frame-state-blocked a")).toHaveTextContent("다시 시도")
    expect(container.querySelectorAll(".data-table-frame")).toHaveLength(5)
    expect(container.querySelectorAll(".data-table-state-ready")).toHaveLength(1)
    expect(container.querySelectorAll(".data-table-state-partial")).toHaveLength(1)
    expect(container.querySelectorAll(".data-table-state-empty")).toHaveLength(1)
    expect(screen.getAllByRole("button", { name: "소스 정렬" })).toHaveLength(5)
    expect(
      screen.getByRole("button", { name: "소스 상태 밀도 50행 다음 페이지" }),
    ).toBeInTheDocument()
    for (const link of container.querySelectorAll(".evidence-rail[data-preview-state] a")) {
      const href = link.getAttribute("href")
      expect(href === null ? null : container.querySelector(href)).toBeTruthy()
    }
    expect(container.querySelector('a[href="https://www.reddit.com/"]')).toBeInTheDocument()
    expect(container.querySelectorAll(".post-row")).toHaveLength(4)
  }, 15000)

  it("reuses the chart retry control through deterministic validation states", async () => {
    render(<DesignSystemPage />)

    const validation = screen.getByLabelText("재시도 상태 검증")
    const retry = screen.getByRole("button", { name: "다시 시도" })
    fireEvent.click(retry)

    expect(validation).toHaveAttribute("data-validation-retry-request-count", "1")
    expect(validation).toHaveAttribute("data-validation-retry-state", "loading")
    await waitFor(() => expect(validation).toHaveAttribute("data-validation-retry-state", "ready"))
    expect(document.activeElement).toHaveAttribute("id", "main-content")
  }, 15000)
})

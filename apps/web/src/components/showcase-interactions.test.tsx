import "@testing-library/jest-dom/vitest"

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ShowcaseInteractions } from "./showcase-interactions"

afterEach(() => {
  document.body.innerHTML = ""
})

describe("showcase modal keyboard contract", () => {
  it("traps Tab focus and restores the trigger on Escape", async () => {
    render(<ShowcaseInteractions />)
    const trigger = screen.getByRole("button", { name: "명령 팔레트 열기" })
    fireEvent.click(trigger)

    const dialog = screen.getByRole("dialog", { name: "검증 명령 팔레트" })
    const close = screen.getByRole("button", { name: "닫기" })
    const input = screen.getByPlaceholderText("페이지 또는 동작 검색")
    const confirm = screen.getByRole("button", { name: "확인" })
    expect(document.activeElement).toBe(close)

    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(input)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(confirm)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(close)
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(confirm)

    fireEvent.keyDown(dialog, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})

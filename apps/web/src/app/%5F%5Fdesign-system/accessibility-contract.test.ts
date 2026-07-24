import "@testing-library/jest-dom/vitest"

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it } from "vitest"
import { z } from "zod"

import DesignSystemPage from "./page"

const RuntimeEvidenceSchema = z.object({
  results: z.array(
    z.object({
      screenshotBytes: z.number().positive(),
      state: z.object({
        after: z.object({ innerWidth: z.number().positive(), scrollWidth: z.number().positive() }),
        before: z.object({ innerWidth: z.number().positive(), scrollWidth: z.number().positive() }),
        filterExpanded: z.enum(["true", "false"]),
      }),
      viewport: z.object({ height: z.number().positive(), width: z.number().positive() }),
    }),
  ),
})

afterEach(cleanup)

describe("design-system accessibility and adaptive contracts", () => {
  it("exposes skip targets, labelled charts, and a keyboard-operable modal on the rendered page", async () => {
    render(createElement(DesignSystemPage))

    const skipLinks = screen.getAllByRole("link", { name: "본문으로 건너뛰기" })
    expect(skipLinks).toHaveLength(3)
    for (const skipLink of skipLinks) {
      const target = document.getElementById(skipLink.getAttribute("href")?.slice(1) ?? "")
      expect(target).toHaveAttribute("tabindex", "-1")
    }
    expect(screen.getByRole("img", { name: "Line · 정상: 월, 화, 수" })).toBeInTheDocument()

    const trigger = screen.getByRole("button", { name: "검증 대화상자 열기" })
    fireEvent.click(trigger)
    const dialog = screen.getByRole("dialog", { name: "검증 대화상자" })
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(screen.getByRole("button", { name: "닫기" })).toHaveFocus()

    fireEvent.keyDown(dialog, { key: "Escape" })
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  }, 15000)

  it("records non-overflowing real-browser captures at each supported viewport", () => {
    const raw = readFileSync(
      resolve(process.cwd(), "../../.omo/evidence/phase4-web-final/design-system-runtime.json"),
      "utf8",
    )
    const runtimeEvidence = RuntimeEvidenceSchema.parse(JSON.parse(raw))

    expect(runtimeEvidence.results.map((result) => result.viewport.width)).toEqual([375, 768, 1280])
    for (const result of runtimeEvidence.results) {
      expect(result.screenshotBytes).toBeGreaterThan(0)
      expect(result.state.before.scrollWidth).toBeLessThanOrEqual(result.state.before.innerWidth)
      expect(result.state.after.scrollWidth).toBeLessThanOrEqual(result.state.after.innerWidth)
    }
  })
})

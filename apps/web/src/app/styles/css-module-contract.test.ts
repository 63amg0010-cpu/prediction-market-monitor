import { createHash } from "node:crypto"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { describe, expect, it } from "vitest"

const stylesDirectory = resolve(process.cwd(), "src/app/styles")
const source = (filename: string) => readFileSync(resolve(stylesDirectory, filename), "utf8")
const nonCommentLines = (css: string) =>
  css.split("\n").filter((line) => {
    const trimmed = line.trim()
    return trimmed.length > 0 && !trimmed.startsWith("/*") && !trimmed.startsWith("*")
  })
const sha256 = (css: string) =>
  createHash("sha256").update(css.replace(/\r\n/g, "\n")).digest("hex")

describe("CSS module split contract", () => {
  it("keeps the global stylesheet import order explicit and every stylesheet reviewable", () => {
    const expectedImports = [
      "./tokens.css",
      "./base.css",
      "./shell.css",
      "./components.css",
      "./components-01.css",
      "./components-02.css",
      "./components-03.css",
      "./components-04.css",
      "./responsive.css",
      "./responsive-01.css",
    ]
    const actualImports = [...source("globals.css").matchAll(/@import\s+"([^"]+)";/g)].map(
      (match) => match[1],
    )
    const stylesheetNames = expectedImports.map((stylesheet) => stylesheet.slice(2))

    expect(actualImports).toEqual(expectedImports)
    for (const stylesheetName of stylesheetNames) {
      const lines = nonCommentLines(source(stylesheetName))
      expect(lines.length).toBeGreaterThan(0)
      expect(lines.length).toBeLessThanOrEqual(250)
    }
  })

  it("keeps the original component and responsive cascade stream ordered", () => {
    const components = [
      "components.css",
      "components-01.css",
      "components-02.css",
      "components-03.css",
      "components-04.css",
    ]
      .map(source)
      .join("")
    const responsive = ["responsive.css", "responsive-01.css"].map(source).join("")

    expect(sha256(components)).toBe(
      "37e2bcf98428c7c057a3d7815b7c4bafac917bfadbbd3afaf7d146c456f69a47",
    )
    expect(sha256(responsive)).toBe(
      "2d42d53e34cd2911bc0aef5cf9623928c2a18951a3f047f3f4401d1bd26b50c3",
    )
  })
})

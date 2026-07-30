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
      "7f123d490286d062eeda6a1eab0c67d87cae57378c0d78f32285c0c0f86af897",
    )
    expect(sha256(responsive)).toBe(
      "9380b0fc63a97bc9ec04c51d844245ca21e0a7233cac53b87eb974c9587da636",
    )
  })
})

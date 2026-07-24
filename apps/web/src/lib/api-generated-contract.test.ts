import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

import { describe, expect, it } from "vitest"

const generatedPath = resolve(process.cwd(), "src/lib/api.generated.ts")
const generatorPath = resolve(process.cwd(), "node_modules/openapi-typescript/bin/cli.js")
const normalizerPath = resolve(process.cwd(), "scripts/normalize-api-generated.mjs")
const sourcePath = "../api/openapi.json"
const generatedSha256 = (): string =>
  createHash("sha256").update(readFileSync(generatedPath)).digest("hex")
const createTemporaryOutput = (): readonly [string, string] => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "prediction-market-openapi-contract-"))
  return [temporaryDirectory, join(temporaryDirectory, "api.generated.ts")]
}

describe("generated OpenAPI response contract", () => {
  it("recreates the committed generated surface byte-for-byte without changing the committed file", () => {
    const committedBytes = readFileSync(generatedPath)
    const committedSha256 = generatedSha256()
    const [temporaryDirectory, temporaryOutputPath] = createTemporaryOutput()

    try {
      execFileSync(process.execPath, [generatorPath, sourcePath, "-o", temporaryOutputPath], {
        cwd: process.cwd(),
        stdio: "pipe",
      })
      execFileSync(process.execPath, [normalizerPath, temporaryOutputPath], {
        cwd: process.cwd(),
        stdio: "pipe",
      })

      expect(readFileSync(temporaryOutputPath)).toEqual(committedBytes)
    } finally {
      rmSync(temporaryDirectory, { force: true, recursive: true })
    }

    expect(existsSync(temporaryDirectory)).toBe(false)
    expect(generatedSha256()).toBe(committedSha256)
  }, 60000)

  it("keeps the committed generated file unchanged and cleans up after generator failure", () => {
    const committedSha256 = generatedSha256()
    const [temporaryDirectory, temporaryOutputPath] = createTemporaryOutput()

    try {
      expect(() =>
        execFileSync(
          process.execPath,
          [generatorPath, "missing-openapi-source.json", "-o", temporaryOutputPath],
          {
            cwd: process.cwd(),
            stdio: "pipe",
          },
        ),
      ).toThrow()
    } finally {
      rmSync(temporaryDirectory, { force: true, recursive: true })
    }

    expect(existsSync(temporaryDirectory)).toBe(false)
    expect(generatedSha256()).toBe(committedSha256)
  }, 60000)
})

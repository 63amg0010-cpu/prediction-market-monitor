import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"
import { z } from "zod"

import { SearchFoldV1Error, searchFoldV1, setRawSearchParamV1 } from "./search-fold-v1"

const ValidVectorSchema = z.object({
  name: z.string(),
  input: z.string(),
  folded_value: z.string(),
  scalar_count: z.number().int(),
  server_like_pattern: z.string(),
})
const InvalidVectorSchema = z.object({
  name: z.string(),
  input: z.string(),
  utf16_code_units: z.array(z.number().int()).optional(),
  reason: z.enum(["scalar_count", "unicode_scalar"]),
})
const FixtureSchema = z.object({
  version: z.literal("search_fold_v1"),
  valid_vectors: z.array(ValidVectorSchema),
  invalid_inputs: z.array(InvalidVectorSchema),
})

const fixture = FixtureSchema.parse(
  JSON.parse(
    readFileSync(resolve(process.cwd(), "../../contracts/search-fold-v1-vectors.json"), "utf8"),
  ),
)

describe("searchFoldV1", () => {
  for (const vector of fixture.valid_vectors) {
    it(`matches shared vector ${vector.name}`, () => {
      // Given: a versioned cross-language input.
      // When: the Web validation fold is applied.
      const result = searchFoldV1(vector.input)

      // Then: only the fold and Unicode scalar count are reproduced client-side.
      expect(result).toEqual({
        value: vector.folded_value,
        scalarCount: vector.scalar_count,
      })
    })
  }

  for (const vector of fixture.invalid_inputs) {
    it(`rejects invalid shared vector ${vector.name}`, () => {
      // Given: a malformed or out-of-bound URL search value.
      const input =
        vector.utf16_code_units === undefined
          ? vector.input
          : String.fromCharCode(...vector.utf16_code_units)
      let captured: unknown

      // When: client validation rejects the same input class as the API.
      try {
        searchFoldV1(input)
      } catch (error) {
        captured = error
      }

      // Then: the rejection carries the vector's exact documented reason.
      expect(captured).toBeInstanceOf(SearchFoldV1Error)
      if (captured instanceof SearchFoldV1Error) {
        expect(captured.reason).toBe(vector.reason)
      }
    })
  }

  it("forwards raw URL text without server LIKE escaping", () => {
    // Given: a URL value whose folded server pattern contains literal wildcards.
    const vector = fixture.valid_vectors.find((item) => item.name === "wildcards_are_literal")
    expect(vector).toBeDefined()
    if (vector === undefined) {
      return
    }
    const parameters = new URLSearchParams()

    // When: the validated search is forwarded toward the API.
    setRawSearchParamV1(parameters, vector.input)

    // Then: URL decoding recovers the raw client value, never the server pattern.
    expect(parameters.get("search")).toBe(vector.input)
    expect(parameters.get("search")).not.toBe(vector.server_like_pattern)
  })
})

export type SearchFoldV1Result = {
  readonly value: string
  readonly scalarCount: number
}

export type SearchFoldV1Reason = "scalar_count" | "unicode_scalar"

export class SearchFoldV1Error extends Error {
  readonly reason: SearchFoldV1Reason

  constructor(reason: SearchFoldV1Reason) {
    super(reason)
    this.name = "SearchFoldV1Error"
    this.reason = reason
  }
}

const ASCII_EDGE_WHITESPACE = "\t\n\v\f\r "
const UNPAIRED_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/

function trimAsciiEdges(input: string): string {
  let start = 0
  let end = input.length
  while (start < end && ASCII_EDGE_WHITESPACE.includes(input.charAt(start))) {
    start += 1
  }
  while (end > start && ASCII_EDGE_WHITESPACE.includes(input.charAt(end - 1))) {
    end -= 1
  }
  return input.slice(start, end)
}

export function searchFoldV1(input: string): SearchFoldV1Result {
  if (UNPAIRED_SURROGATE.test(input)) {
    throw new SearchFoldV1Error("unicode_scalar")
  }
  const value = trimAsciiEdges(input)
    .normalize("NFC")
    .replace(/[A-Z]/g, (character) => character.toLowerCase())
  const scalarCount = [...value].length
  if (scalarCount < 2 || scalarCount > 100) {
    throw new SearchFoldV1Error("scalar_count")
  }
  return { value, scalarCount }
}

export function setRawSearchParamV1(parameters: URLSearchParams, rawInput: string): void {
  searchFoldV1(rawInput)
  parameters.set("search", rawInput)
}

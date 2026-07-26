import "server-only"

import { BoundaryError } from "./api-error"
import { readServerEnvironment } from "./server-env"

export const UPSTREAM_REQUEST_TIMEOUT_MS = 30_000

export function apiUrl(path: string): URL {
  return new URL(path, readServerEnvironment().apiBaseUrl)
}

export async function runNetworkRequest(request: () => Promise<Response>): Promise<Response> {
  try {
    return await request()
  } catch (error) {
    if (error instanceof BoundaryError) {
      throw error
    }
    if (error instanceof Error) {
      throw new BoundaryError("network", "upstream service is unreachable")
    }
    throw error
  }
}

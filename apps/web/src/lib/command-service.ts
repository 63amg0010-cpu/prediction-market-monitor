import "server-only"

import ky from "ky"

import { BoundaryError, upstreamError } from "./api-error"
import { getBffAdminToken } from "./bff-server"
import { type CollectionRetryResponse, CollectionRetryResponseSchema } from "./command-contract"
import { apiUrl, runNetworkRequest, UPSTREAM_REQUEST_TIMEOUT_MS } from "./server-http"

type CollectionRetryInput = {
  readonly requestId: string
  readonly sourceId: string
  readonly sessionToken: string
  readonly csrfToken: string
  readonly origin: string | null
  readonly referer: string | null
}

export async function retryCollection(
  input: CollectionRetryInput,
): Promise<CollectionRetryResponse> {
  const token = await getBffAdminToken()
  const headers: Record<string, string> & { origin?: string; referer?: string } = {
    authorization: `Bearer ${token}`,
    "x-admin-session": input.sessionToken,
    "x-csrf-token": input.csrfToken,
    "cache-control": "no-store",
  }
  if (input.origin !== null) headers.origin = input.origin
  if (input.referer !== null) headers.referer = input.referer
  const response = await runNetworkRequest(() =>
    ky.post(apiUrl("/v1/commands/collection-retry"), {
      timeout: UPSTREAM_REQUEST_TIMEOUT_MS,
      retry: 0,
      throwHttpErrors: false,
      headers,
      json: {
        request_id: input.requestId,
        source_ids: [input.sourceId],
        reason: "관리자 운영 화면에서 수집 오류 재시도",
      },
    }),
  )
  if (!response.ok) throw await upstreamError(response)
  let raw: unknown
  try {
    raw = await response.json()
  } catch (error) {
    if (error instanceof Error) {
      throw new BoundaryError("invalid_response", "collection retry response is invalid JSON")
    }
    throw error
  }
  const parsed = CollectionRetryResponseSchema.safeParse(raw)
  if (!parsed.success) {
    throw new BoundaryError("invalid_response", "collection retry response contract drifted")
  }
  return parsed.data
}

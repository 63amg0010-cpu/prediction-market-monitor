import "server-only"

import ky from "ky"

import { BffExchangeResponseSchema } from "./api-contract"
import { BoundaryError, upstreamError } from "./api-error"
import {
  ADMIN_COMMAND_SCOPES,
  BFF_READ_SCOPES,
  type BffScopeSet,
  createBffTokenProvider,
  type ExchangedToken,
} from "./bff-token"
import { readServerEnvironment } from "./server-env"
import { runNetworkRequest, UPSTREAM_REQUEST_TIMEOUT_MS } from "./server-http"

async function exchangeToken(scopes: BffScopeSet): Promise<ExchangedToken> {
  const environment = readServerEnvironment()
  const response = await runNetworkRequest(() =>
    ky.post(new URL("/v1/service-tokens/bff/exchange", environment.apiBaseUrl), {
      timeout: UPSTREAM_REQUEST_TIMEOUT_MS,
      retry: 0,
      throwHttpErrors: false,
      headers: {
        authorization: `Bearer ${environment.bffClientCredential}`,
        "x-deployment-identity": environment.deploymentIdentity,
        "cache-control": "no-store",
      },
      json: {
        credential_version: environment.bffCredentialVersion,
        request_nonce: crypto.randomUUID(),
        requested_scopes: [...scopes],
      },
    }),
  )
  if (!response.ok) {
    throw await upstreamError(response)
  }
  const raw: unknown = await response.json().catch(() => null)
  const parsed = BffExchangeResponseSchema.safeParse(raw)
  if (!parsed.success) {
    throw new BoundaryError("invalid_response", "service token response is invalid")
  }
  return {
    accessToken: parsed.data.access_token,
    expiresAt: parsed.data.expires_at,
    scope: parsed.data.scope,
  }
}

const provider = createBffTokenProvider({ exchange: exchangeToken, now: Date.now })

export function getBffReadToken(): Promise<string> {
  return provider.get(BFF_READ_SCOPES)
}

export function getBffAdminToken(): Promise<string> {
  return provider.get(ADMIN_COMMAND_SCOPES)
}

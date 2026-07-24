import "server-only"

import ky from "ky"

import {
  type AdminSession,
  AdminSessionResponseSchema,
  normalizeAdminSession,
} from "./api-contract"
import { BoundaryError, upstreamError } from "./api-error"
import { getBffReadToken } from "./bff-server"
import { apiUrl, runNetworkRequest } from "./server-http"

export type LoginInput = {
  readonly password: string
  readonly clientIp: string
}

export type LogoutInput = {
  readonly sessionToken: string
  readonly csrfToken: string
  readonly origin: string | null
  readonly referer: string | null
}

async function parseSession(response: Response): Promise<AdminSession> {
  if (!response.ok) {
    throw await upstreamError(response)
  }
  const raw: unknown = await response.json().catch(() => null)
  const parsed = AdminSessionResponseSchema.safeParse(raw)
  if (!parsed.success) {
    throw new BoundaryError("invalid_response", "administrator session response is invalid")
  }
  return normalizeAdminSession(parsed.data)
}

export async function loginAdmin(input: LoginInput): Promise<AdminSession> {
  const bffToken = await getBffReadToken()
  const response = await runNetworkRequest(() =>
    ky.post(apiUrl("/v1/auth/login"), {
      timeout: 10_000,
      retry: 0,
      throwHttpErrors: false,
      headers: { authorization: `Bearer ${bffToken}`, "cache-control": "no-store" },
      json: { password: input.password, client_ip: input.clientIp },
    }),
  )
  const session = await parseSession(response)
  if (session.sessionToken === null) {
    throw new BoundaryError("invalid_response", "login response omitted the session token")
  }
  return session
}

export async function validateAdminSession(sessionToken: string): Promise<AdminSession> {
  const bffToken = await getBffReadToken()
  const response = await runNetworkRequest(() =>
    ky.get(apiUrl("/v1/auth/session"), {
      timeout: 10_000,
      retry: 0,
      throwHttpErrors: false,
      headers: {
        authorization: `Bearer ${bffToken}`,
        "x-admin-session": sessionToken,
        "cache-control": "no-store",
      },
    }),
  )
  return parseSession(response)
}

export async function logoutAdmin(input: LogoutInput): Promise<void> {
  const bffToken = await getBffReadToken()
  const headers: Record<string, string> & { origin?: string; referer?: string } = {
    authorization: `Bearer ${bffToken}`,
    "x-admin-session": input.sessionToken,
    "x-csrf-token": input.csrfToken,
    "cache-control": "no-store",
  }
  if (input.origin !== null) {
    headers.origin = input.origin
  }
  if (input.referer !== null) {
    headers.referer = input.referer
  }
  const response = await runNetworkRequest(() =>
    ky.post(apiUrl("/v1/auth/logout"), {
      timeout: 10_000,
      retry: 0,
      throwHttpErrors: false,
      headers,
    }),
  )
  if (!response.ok) {
    throw await upstreamError(response)
  }
}

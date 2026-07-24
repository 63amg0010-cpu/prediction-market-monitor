import { isIP } from "node:net"
import type { NextRequest } from "next/server"

import { z } from "zod"

const UrlSchema = z.string().url()

const LoopbackHosts = new Set(["localhost", "127.0.0.1", "::1"])

type RequestSecurityEnvironment = Pick<NodeJS.ProcessEnv, "NODE_ENV"> &
  Partial<Pick<NodeJS.ProcessEnv, "VERCEL" | "WEB_PUBLIC_ORIGIN">>

const UnavailableClientIp = "unavailable"

function parsedUrl(value: string | null): URL | null {
  const parsed = UrlSchema.safeParse(value)
  return parsed.success ? new URL(parsed.data) : null
}

function normalizedHostname(url: URL): string {
  return url.hostname.replace(/^\[|\]$/g, "").toLowerCase()
}

function requestAuthority(request: NextRequest): URL | null {
  const host = request.headers.get("host") ?? request.nextUrl.host
  return parsedUrl(`${request.nextUrl.protocol}//${host}`)
}

function isLocalAlias(left: URL, right: URL): boolean {
  return (
    LoopbackHosts.has(normalizedHostname(left)) &&
    LoopbackHosts.has(normalizedHostname(right)) &&
    left.protocol === right.protocol &&
    left.port === right.port
  )
}

function matchesProductionOrigin(
  candidate: URL,
  request: NextRequest,
  environment: RequestSecurityEnvironment,
): boolean {
  const configured = parsedUrl(environment.WEB_PUBLIC_ORIGIN ?? null)
  const host = request.headers.get("host")
  return (
    configured !== null &&
    host !== null &&
    candidate.origin === configured.origin &&
    host.toLowerCase() === configured.host.toLowerCase() &&
    request.nextUrl.protocol === configured.protocol
  )
}

export function isSameOrigin(
  request: NextRequest,
  environment: RequestSecurityEnvironment = process.env,
): boolean {
  const origin = request.headers.get("origin")
  const candidate = parsedUrl(origin ?? request.headers.get("referer"))
  if (candidate === null) return false

  if (environment.NODE_ENV === "production") {
    return matchesProductionOrigin(candidate, request, environment)
  }

  const authority = requestAuthority(request)
  return (
    authority !== null &&
    (candidate.origin === authority.origin || isLocalAlias(candidate, authority))
  )
}

export function clientIp(
  request: NextRequest,
  environment: RequestSecurityEnvironment = process.env,
): string {
  if (environment.VERCEL !== "1") return UnavailableClientIp

  const forwarded = request.headers.get("x-vercel-forwarded-for")
  return forwarded !== null && isIP(forwarded) !== 0 ? forwarded : UnavailableClientIp
}

export function isCrossSite(request: NextRequest): boolean {
  return request.headers.get("sec-fetch-site") === "cross-site"
}

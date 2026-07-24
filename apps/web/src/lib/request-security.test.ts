import { NextRequest } from "next/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import { clientIp, isSameOrigin } from "./request-security"

function mutationRequest(input: {
  readonly url: string
  readonly host: string
  readonly origin: string
  readonly forwardedHost?: string
}): NextRequest {
  const headers = new Headers({ host: input.host, origin: input.origin })
  if (input.forwardedHost !== undefined) {
    headers.set("x-forwarded-host", input.forwardedHost)
  }
  return new NextRequest(input.url, { method: "POST", headers })
}

function requestWithHeaders(headers: HeadersInit): NextRequest {
  return new NextRequest("https://dashboard.example.test/api/auth/login", {
    method: "POST",
    headers,
  })
}

describe("same-origin request validation", () => {
  afterEach(() => vi.unstubAllEnvs())

  it.each([
    ["http://localhost:3104/api/auth/login", "localhost:3104", "http://127.0.0.1:3104"],
    ["http://127.0.0.1:3104/api/auth/login", "127.0.0.1:3104", "http://localhost:3104"],
  ])("allows loopback aliases on the same scheme and port in local mode", (url, host, origin) => {
    vi.stubEnv("NODE_ENV", "development")
    expect(isSameOrigin(mutationRequest({ url, host, origin }))).toBe(true)
  })

  it("rejects a loopback alias on a different port", () => {
    vi.stubEnv("NODE_ENV", "development")
    expect(
      isSameOrigin(
        mutationRequest({
          url: "http://localhost:3104/api/auth/login",
          host: "localhost:3104",
          origin: "http://127.0.0.1:3105",
        }),
      ),
    ).toBe(false)
  })

  it("rejects a hostname that merely ends with a loopback label", () => {
    vi.stubEnv("NODE_ENV", "development")
    expect(
      isSameOrigin(
        mutationRequest({
          url: "http://localhost:3104/api/auth/login",
          host: "localhost:3104",
          origin: "http://localhost.evil.test:3104",
        }),
      ),
    ).toBe(false)
  })

  it("ignores forwarded-host spoofing", () => {
    vi.stubEnv("NODE_ENV", "development")
    expect(
      isSameOrigin(
        mutationRequest({
          url: "https://dashboard.example.test/api/auth/login",
          host: "dashboard.example.test",
          origin: "https://attacker.test",
          forwardedHost: "localhost:3104",
        }),
      ),
    ).toBe(false)
  })

  it("requires the exact configured origin and host in production", () => {
    vi.stubEnv("NODE_ENV", "production")
    vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3104")
    expect(
      isSameOrigin(
        mutationRequest({
          url: "http://localhost:3104/api/auth/login",
          host: "localhost:3104",
          origin: "http://127.0.0.1:3104",
        }),
      ),
    ).toBe(false)
    expect(
      isSameOrigin(
        mutationRequest({
          url: "http://localhost:3104/api/auth/login",
          host: "localhost:3104",
          origin: "http://localhost:3104",
        }),
      ),
    ).toBe(true)
  })

  it("uses the supplied production environment instead of the host process environment", () => {
    const request = mutationRequest({
      url: "http://localhost:3104/api/auth/login",
      host: "localhost:3104",
      origin: "http://127.0.0.1:3104",
    })

    expect(
      isSameOrigin(request, {
        NODE_ENV: "production",
        WEB_PUBLIC_ORIGIN: "http://localhost:3104",
      }),
    ).toBe(false)
  })
})

describe("login client identity", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("uses one conservative bucket when local forwarding headers are spoofed", () => {
    vi.stubEnv("VERCEL", "")
    const firstSpoof = clientIp(requestWithHeaders({ "x-forwarded-for": "203.0.113.7" }))
    const secondSpoof = clientIp(requestWithHeaders({ "x-forwarded-for": "198.51.100.9" }))

    expect(firstSpoof).toBe("unavailable")
    expect(secondSpoof).toBe("unavailable")
  })

  it("uses only Vercel's exact forwarded client IP in a Vercel deployment", () => {
    vi.stubEnv("VERCEL", "1")

    expect(
      clientIp(
        requestWithHeaders({
          "x-forwarded-for": "198.51.100.9",
          "x-vercel-forwarded-for": "203.0.113.7",
        }),
      ),
    ).toBe("203.0.113.7")
  })

  it("rejects a forwarded client IP list even in a Vercel deployment", () => {
    vi.stubEnv("VERCEL", "1")

    expect(
      clientIp(requestWithHeaders({ "x-vercel-forwarded-for": "203.0.113.7, 198.51.100.9" })),
    ).toBe("unavailable")
  })

  it("uses the supplied deployment environment instead of the host process environment", () => {
    expect(
      clientIp(requestWithHeaders({ "x-vercel-forwarded-for": "203.0.113.7" }), {
        NODE_ENV: "test",
        VERCEL: "1",
      }),
    ).toBe("203.0.113.7")
  })
})

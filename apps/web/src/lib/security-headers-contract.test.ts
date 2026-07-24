import { afterEach, describe, expect, it, vi } from "vitest"

import nextConfig from "../../next.config.js"

type Header = {
  readonly key: string
  readonly value: string
}

type HeaderRule = {
  readonly source: string
  readonly headers: readonly Header[]
}

function allHeaderValues(rules: readonly HeaderRule[]): ReadonlyMap<string, string> {
  const globalRule = rules.find((rule) => rule.source === "/(.*)")
  if (globalRule === undefined) {
    throw new Error("global security header rule is missing")
  }
  return new Map(globalRule.headers.map((header) => [header.key, header.value]))
}

async function configuredHeaders(): Promise<readonly HeaderRule[]> {
  if (nextConfig.headers === undefined) {
    throw new Error("Next headers configuration is missing")
  }
  return nextConfig.headers()
}

describe("global browser security headers", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("sets a production CSP and baseline browser protections", async () => {
    vi.stubEnv("NODE_ENV", "production")
    vi.stubEnv("VERCEL_ENV", "production")
    const rules = await configuredHeaders()
    const headers = allHeaderValues(rules)
    const csp = headers.get("Content-Security-Policy") ?? ""

    expect(csp).toContain("default-src 'self'")
    expect(csp).toContain("object-src 'none'")
    expect(csp).toContain("base-uri 'self'")
    expect(csp).toContain("frame-ancestors 'none'")
    expect(csp).not.toContain("'unsafe-eval'")
    expect(headers.get("X-Content-Type-Options")).toBe("nosniff")
    expect(headers.get("Referrer-Policy")).toBe("strict-origin-when-cross-origin")
    expect(headers.get("Permissions-Policy")).toContain("camera=()")
    expect(headers.get("Strict-Transport-Security")).toContain("max-age=")
  })

  it("keeps local development HTTP free of HSTS while allowing Next development scripts", async () => {
    vi.stubEnv("NODE_ENV", "development")
    vi.stubEnv("VERCEL_ENV", "")
    const rules = await configuredHeaders()
    const headers = allHeaderValues(rules)

    expect(headers.get("Strict-Transport-Security")).toBeUndefined()
    expect(headers.get("Content-Security-Policy")).toContain("'unsafe-eval'")
  })
})

import { describe, expect, it, vi } from "vitest"

import { BFF_READ_SCOPES, createBffTokenProvider } from "./bff-token"

describe("BFF service-token cache", () => {
  it("caches an exact scope set for no longer than 240 seconds", async () => {
    let now = Date.parse("2026-07-22T00:00:00Z")
    const exchange = vi.fn().mockResolvedValue({
      accessToken: "server-only-token",
      expiresAt: "2026-07-22T00:05:00Z",
      scope: BFF_READ_SCOPES,
    })
    const provider = createBffTokenProvider({ exchange, now: () => now })

    await expect(provider.get(BFF_READ_SCOPES)).resolves.toBe("server-only-token")
    now += 239_000
    await expect(provider.get(BFF_READ_SCOPES)).resolves.toBe("server-only-token")
    expect(exchange).toHaveBeenCalledTimes(1)

    now += 2_000
    await expect(provider.get(BFF_READ_SCOPES)).resolves.toBe("server-only-token")
    expect(exchange).toHaveBeenCalledTimes(2)
  })

  it("deduplicates concurrent exchanges", async () => {
    const exchange = vi.fn().mockResolvedValue({
      accessToken: "one-token",
      expiresAt: "2026-07-22T00:05:00Z",
      scope: BFF_READ_SCOPES,
    })
    const provider = createBffTokenProvider({
      exchange,
      now: () => Date.parse("2026-07-22T00:00:00Z"),
    })

    await Promise.all([provider.get(BFF_READ_SCOPES), provider.get(BFF_READ_SCOPES)])
    expect(exchange).toHaveBeenCalledTimes(1)
  })
})

import { beforeEach, describe, expect, it, vi } from "vitest"

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }))

vi.mock("server-only", () => ({}))
vi.mock("ky", () => ({ default: { post: postMock } }))
vi.mock("./server-env", () => ({
  readServerEnvironment: () => ({
    apiBaseUrl: "https://api.example.test",
    bffClientCredential: "credential-with-at-least-thirty-two-characters",
    bffCredentialVersion: "v1",
    deploymentIdentity: "test-deployment",
  }),
}))

import { getBffReadToken } from "./bff-server"

describe("BFF server exchange", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    postMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "server-only-token",
          token_type: "Bearer",
          expires_at: "2099-07-26T00:05:00Z",
          scope: ["bff:auth", "bff:read"],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    )
  })

  it("allows the upstream API enough time to complete a cold start", async () => {
    // Given: a fresh BFF instance without a cached service token.
    // When: the BFF exchanges its deployment credential for a service token.
    await expect(getBffReadToken()).resolves.toBe("server-only-token")

    // Then: the upstream timeout exceeds the observed twelve-second cold start.
    expect(postMock).toHaveBeenCalledWith(
      new URL("/v1/service-tokens/bff/exchange", "https://api.example.test"),
      expect.objectContaining({ timeout: 30_000 }),
    )
  })
})

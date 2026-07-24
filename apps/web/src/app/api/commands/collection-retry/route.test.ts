import { NextRequest } from "next/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { validateAdminSession } from "../../../../lib/auth-api"
import { retryCollection } from "../../../../lib/command-service"
import { POST } from "./route"

vi.mock("../../../../lib/auth-api", () => ({ validateAdminSession: vi.fn() }))
vi.mock("../../../../lib/command-service", () => ({ retryCollection: vi.fn() }))

const requestId = "55555555-5555-4555-8555-555555555555"
const sourceId = "22222222-2222-4222-8222-222222222222"

function commandRequest(origin = "https://dashboard.example.test") {
  return new NextRequest("https://dashboard.example.test/api/commands/collection-retry", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      cookie: "__Host-monitor_session=opaque-session-secret",
      origin,
      referer: "https://dashboard.example.test/status",
    },
    body: JSON.stringify({ requestId, sourceId }),
  })
}

describe("collection retry BFF route", () => {
  beforeEach(() => vi.clearAllMocks())

  it("validates the real admin session and forwards rotated session plus CSRF evidence", async () => {
    vi.mocked(validateAdminSession).mockResolvedValue({
      sessionToken: "rotated-session-secret",
      expiresAt: "2026-07-22T08:00:00Z",
      csrfToken: "csrf-current-bucket",
      rotated: true,
    })
    vi.mocked(retryCollection).mockResolvedValue({
      command_id: "66666666-6666-4666-8666-666666666666",
      created: true,
    })

    const response = await POST(commandRequest())

    expect(response.status).toBe(202)
    expect(validateAdminSession).toHaveBeenCalledWith("opaque-session-secret")
    expect(retryCollection).toHaveBeenCalledWith({
      requestId,
      sourceId,
      sessionToken: "rotated-session-secret",
      csrfToken: "csrf-current-bucket",
      origin: "https://dashboard.example.test",
      referer: "https://dashboard.example.test/status",
    })
    expect(response.headers.get("set-cookie")).toContain("__Host-monitor_session=")
    await expect(response.text()).resolves.not.toContain("session-secret")
  })

  it("rejects cross-origin mutation before validating the session", async () => {
    const response = await POST(commandRequest("https://attacker.test"))

    expect(response.status).toBe(403)
    expect(validateAdminSession).not.toHaveBeenCalled()
    expect(retryCollection).not.toHaveBeenCalled()
  })
})

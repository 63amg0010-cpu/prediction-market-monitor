import { NextRequest } from "next/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { loginAdmin } from "../../../../lib/auth-api"
import { POST } from "./route"

vi.mock("../../../../lib/auth-api", () => ({ loginAdmin: vi.fn() }))

describe("login BFF route", () => {
  beforeEach(() => vi.clearAllMocks())

  it("sets an opaque Secure HttpOnly Strict host cookie without leaking the session", async () => {
    vi.mocked(loginAdmin).mockResolvedValue({
      sessionToken: "opaque-session-secret",
      expiresAt: "2026-07-22T08:00:00Z",
      csrfToken: "csrf-current-bucket",
      rotated: false,
    })
    const request = new NextRequest("https://dashboard.example.test/api/auth/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: "https://dashboard.example.test",
        "x-forwarded-for": "203.0.113.7",
      },
      body: JSON.stringify({ password: "administrator-password" }),
    })

    const response = await POST(request)
    const cookie = response.headers.get("set-cookie") ?? ""
    expect(response.status).toBe(200)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(cookie).toContain("__Host-monitor_session=")
    expect(cookie).toContain("HttpOnly")
    expect(cookie).toContain("Secure")
    expect(cookie).toContain("SameSite=strict")
    expect(cookie).toContain("Path=/")
    await expect(response.text()).resolves.not.toContain("opaque-session-secret")
  })

  it("rejects a cross-origin login before calling the API", async () => {
    const request = new NextRequest("https://dashboard.example.test/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://attacker.test" },
      body: JSON.stringify({ password: "administrator-password" }),
    })
    const response = await POST(request)
    expect(response.status).toBe(403)
    expect(loginAdmin).not.toHaveBeenCalled()
  })
})

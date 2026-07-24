import { type NextRequest, NextResponse } from "next/server"
import { z } from "zod"

import { BoundaryError, publicError } from "../../../../lib/api-error"
import { logoutAdmin } from "../../../../lib/auth-api"
import { isSameOrigin } from "../../../../lib/request-security"
import { clearSessionCookie, SESSION_COOKIE } from "../../../../lib/session-cookie"

const CsrfSchema = z.string().min(1).max(512)

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "same-origin request required" }, { status: 403 })
  }
  const csrf = CsrfSchema.safeParse(request.headers.get("x-csrf-token"))
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value
  if (!csrf.success || sessionToken === undefined) {
    return NextResponse.json({ error: "로그인 또는 CSRF 확인이 필요합니다." }, { status: 403 })
  }
  try {
    await logoutAdmin({
      sessionToken,
      csrfToken: csrf.data,
      origin: request.headers.get("origin"),
      referer: request.headers.get("referer"),
    })
    const response = new NextResponse(null, { status: 204 })
    response.headers.set("cache-control", "no-store")
    clearSessionCookie(response)
    return response
  } catch (error) {
    if (error instanceof BoundaryError) {
      const result = publicError(error)
      return NextResponse.json(result.body, {
        status: result.status,
        headers: { "cache-control": "no-store" },
      })
    }
    throw error
  }
}

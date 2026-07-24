import { type NextRequest, NextResponse } from "next/server"

import { BoundaryError, publicError } from "../../../../lib/api-error"
import { validateAdminSession } from "../../../../lib/auth-api"
import { isCrossSite } from "../../../../lib/request-security"
import {
  clearSessionCookie,
  SESSION_COOKIE,
  setSessionCookie,
} from "../../../../lib/session-cookie"

export async function GET(request: NextRequest): Promise<NextResponse> {
  if (isCrossSite(request)) {
    return NextResponse.json({ authenticated: false }, { status: 403 })
  }
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value
  if (sessionToken === undefined) {
    return NextResponse.json(
      { authenticated: false },
      { status: 401, headers: { "cache-control": "no-store" } },
    )
  }
  try {
    const session = await validateAdminSession(sessionToken)
    const response = NextResponse.json({
      authenticated: true,
      expiresAt: session.expiresAt,
      csrfToken: session.csrfToken,
    })
    response.headers.set("cache-control", "no-store")
    if (session.sessionToken !== null) {
      setSessionCookie(response, session.sessionToken, session.expiresAt)
    }
    return response
  } catch (error) {
    if (error instanceof BoundaryError) {
      const result = publicError(error)
      const response = NextResponse.json(result.body, {
        status: result.status,
        headers: { "cache-control": "no-store" },
      })
      if (error.status === 401 || error.status === 403) {
        clearSessionCookie(response)
      }
      return response
    }
    throw error
  }
}

import { type NextRequest, NextResponse } from "next/server"

import { BoundaryError, publicError } from "../../../lib/api-error"
import { validateAdminSession } from "../../../lib/auth-api"
import { loadDashboard } from "../../../lib/dashboard-service"
import { parseFilters } from "../../../lib/filter-contract"
import { clearSessionCookie, SESSION_COOKIE, setSessionCookie } from "../../../lib/session-cookie"

export async function GET(request: NextRequest): Promise<NextResponse> {
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value
  if (sessionToken === undefined) {
    return NextResponse.json(
      { error: "authentication required" },
      { status: 401, headers: { "cache-control": "no-store" } },
    )
  }
  try {
    const session = await validateAdminSession(sessionToken)
    const state = await loadDashboard(parseFilters(request.nextUrl.searchParams))
    const response = NextResponse.json(state, {
      status: state.kind === "invalid_request" ? 422 : 200,
      headers: { "cache-control": "no-store" },
    })
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

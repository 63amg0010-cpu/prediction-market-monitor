import { type NextRequest, NextResponse } from "next/server"

import { BoundaryError, publicError } from "../../../../lib/api-error"
import { validateAdminSession } from "../../../../lib/auth-api"
import { CollectionRetryBrowserRequestSchema } from "../../../../lib/command-contract"
import { retryCollection } from "../../../../lib/command-service"
import { isSameOrigin } from "../../../../lib/request-security"
import {
  clearSessionCookie,
  SESSION_COOKIE,
  setSessionCookie,
} from "../../../../lib/session-cookie"

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "same-origin request required" }, { status: 403 })
  }
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value
  if (sessionToken === undefined) {
    return NextResponse.json({ error: "authentication required" }, { status: 401 })
  }

  let raw: unknown
  try {
    raw = await request.json()
  } catch (error) {
    if (!(error instanceof Error)) throw error
    return NextResponse.json({ error: "valid JSON required" }, { status: 400 })
  }
  const payload = CollectionRetryBrowserRequestSchema.safeParse(raw)
  if (!payload.success) {
    return NextResponse.json({ error: "valid request and source ids required" }, { status: 422 })
  }

  try {
    const session = await validateAdminSession(sessionToken)
    const result = await retryCollection({
      requestId: payload.data.requestId,
      sourceId: payload.data.sourceId,
      sessionToken: session.sessionToken ?? sessionToken,
      csrfToken: session.csrfToken,
      origin: request.headers.get("origin"),
      referer: request.headers.get("referer"),
    })
    const response = NextResponse.json(result, {
      status: result.created ? 202 : 200,
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
      if (error.status === 401 || error.status === 403) clearSessionCookie(response)
      return response
    }
    throw error
  }
}

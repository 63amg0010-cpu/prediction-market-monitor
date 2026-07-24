import { type NextRequest, NextResponse } from "next/server"

import { SESSION_COOKIE } from "./lib/session-cookie"

export function middleware(request: NextRequest): NextResponse {
  if (request.cookies.get(SESSION_COOKIE) === undefined) {
    const loginUrl = new URL("/login", request.url)
    return NextResponse.redirect(loginUrl)
  }
  return NextResponse.next()
}

export const config = { matcher: ["/", "/posts/:path*", "/reports/:path*", "/status/:path*"] }

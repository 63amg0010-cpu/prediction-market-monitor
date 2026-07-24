import { type NextRequest, NextResponse } from "next/server"
import { z } from "zod"

import { BoundaryError, publicError } from "../../../../lib/api-error"
import { loginAdmin } from "../../../../lib/auth-api"
import { clientIp, isSameOrigin } from "../../../../lib/request-security"
import { setSessionCookie } from "../../../../lib/session-cookie"

const LoginRequestSchema = z.object({ password: z.string().min(1).max(1024) }).strict()

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "same-origin request required" }, { status: 403 })
  }
  const raw: unknown = await request.json().catch(() => null)
  const parsed = LoginRequestSchema.safeParse(raw)
  if (!parsed.success) {
    return NextResponse.json({ error: "비밀번호를 입력해 주세요." }, { status: 422 })
  }
  try {
    const session = await loginAdmin({
      password: parsed.data.password,
      clientIp: clientIp(request),
    })
    const token = session.sessionToken
    if (token === null) {
      return NextResponse.json({ error: "로그인 세션을 만들지 못했습니다." }, { status: 503 })
    }
    const response = NextResponse.json({ authenticated: true, expiresAt: session.expiresAt })
    response.headers.set("cache-control", "no-store")
    setSessionCookie(response, token, session.expiresAt)
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

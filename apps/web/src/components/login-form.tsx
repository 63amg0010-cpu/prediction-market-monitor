"use client"

import { LockKeyOpen } from "@phosphor-icons/react"
import ky from "ky"
import { type FormEvent, type KeyboardEvent, useRef, useState } from "react"
import { z } from "zod"

const LoginSuccessSchema = z
  .object({ authenticated: z.literal(true), expiresAt: z.string() })
  .strict()
const ErrorSchema = z.object({ error: z.string() }).passthrough()

export function LoginForm() {
  const passwordRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setBusy(true)
    setMessage(null)
    const password = passwordRef.current?.value
    if (password === undefined || password.length === 0) {
      setMessage("비밀번호를 입력해 주세요.")
      setBusy(false)
      return
    }
    try {
      const response = await ky.post("/api/auth/login", {
        retry: 0,
        throwHttpErrors: false,
        json: { password },
      })
      const raw: unknown = await response.json().catch(() => null)
      const success = LoginSuccessSchema.safeParse(raw)
      if (response.ok && success.success) {
        window.location.assign("/")
        return
      }
      const failure = ErrorSchema.safeParse(raw)
      setMessage(failure.success ? failure.data.error : "로그인을 완료하지 못했습니다.")
    } catch (caught) {
      if (caught instanceof Error) {
        setMessage("인증 서비스에 연결할 수 없습니다.")
      } else {
        throw caught
      }
    } finally {
      setBusy(false)
    }
  }

  function submitOnEnter(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key !== "Enter") return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  return (
    <form action="/api/auth/login" className="login-form" method="post" onSubmit={submit}>
      <label htmlFor="password">관리자 비밀번호</label>
      <input
        autoComplete="current-password"
        id="password"
        onKeyDown={submitOnEnter}
        ref={passwordRef}
        required
        type="password"
      />
      <button aria-busy={busy} className="button button-primary" disabled={busy} type="submit">
        <LockKeyOpen aria-hidden size={20} />
        {busy ? "확인 중" : "대시보드 열기"}
      </button>
      {message !== null && <p role="alert">{message}</p>}
    </form>
  )
}

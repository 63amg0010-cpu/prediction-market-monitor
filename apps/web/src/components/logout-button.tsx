"use client"

import { SignOut } from "@phosphor-icons/react"
import ky from "ky"
import { useState } from "react"

import { BrowserSessionSchema } from "../lib/api-contract"

export function LogoutButton() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function logout(): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      const sessionResponse = await ky.get("/api/auth/session", {
        retry: 0,
        throwHttpErrors: false,
        headers: { "cache-control": "no-store" },
      })
      if (!sessionResponse.ok) {
        window.location.assign("/login")
        return
      }
      const raw: unknown = await sessionResponse.json()
      const session = BrowserSessionSchema.safeParse(raw)
      if (!session.success) {
        setError("로그아웃 확인 정보를 읽지 못했습니다.")
        return
      }
      const response = await ky.post("/api/auth/logout", {
        retry: 0,
        throwHttpErrors: false,
        headers: { "x-csrf-token": session.data.csrfToken },
      })
      if (!response.ok) {
        setError("로그아웃을 완료하지 못했습니다. 다시 시도해 주세요.")
        return
      }
      window.location.assign("/login")
    } catch (caught) {
      if (caught instanceof Error) {
        setError("로그아웃 서비스에 연결할 수 없습니다.")
        return
      }
      throw caught
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="logout-control">
      <button
        aria-busy={busy}
        className="button button-ghost"
        disabled={busy}
        onClick={logout}
        type="button"
      >
        <SignOut aria-hidden size={18} />
        {busy ? "확인 중" : "로그아웃"}
      </button>
      {error !== null && <span role="alert">{error}</span>}
    </div>
  )
}

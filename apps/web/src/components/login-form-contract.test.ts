import "@testing-library/jest-dom/vitest"

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { LoginForm } from "./login-form"

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }))

vi.mock("ky", () => ({ default: { post: postMock } }))

afterEach(() => {
  cleanup()
  postMock.mockReset()
})

describe("LoginForm progressive security contract", () => {
  it("keeps the no-JavaScript fallback credential-free and POST-only", () => {
    render(createElement(LoginForm))

    const password = screen.getByLabelText("관리자 비밀번호")
    const form = password.closest("form")

    expect(form).toHaveAttribute("action", "/api/auth/login")
    expect(form).toHaveAttribute("method", "post")
    expect(new FormData(form ?? undefined).has("password")).toBe(false)
  })

  it("submits Enter as same-origin JSON, then announces an authentication error without moving focus", async () => {
    postMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "비밀번호가 일치하지 않습니다." }), {
        headers: { "content-type": "application/json" },
        status: 401,
      }),
    )
    render(createElement(LoginForm))

    const password = screen.getByLabelText("관리자 비밀번호")
    fireEvent.change(password, { target: { value: "wrong-password" } })
    password.focus()
    fireEvent.keyDown(password, { key: "Enter" })

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1))
    expect(postMock).toHaveBeenCalledWith("/api/auth/login", {
      json: { password: "wrong-password" },
      retry: 0,
      throwHttpErrors: false,
      timeout: 30_000,
    })
    expect(postMock.mock.calls[0]?.[1]).not.toHaveProperty("headers.x-csrf-token")

    expect(await screen.findByRole("alert")).toHaveTextContent("비밀번호가 일치하지 않습니다.")
    expect(password).toHaveFocus()
  })
})

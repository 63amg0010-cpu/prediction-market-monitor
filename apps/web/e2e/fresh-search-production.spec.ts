import { expect, test } from "@playwright/test"

const READ_ONLY_METHODS = new Set(["GET", "HEAD", "OPTIONS"])

function requiredPassword(): string {
  const value = process.env.PRODUCTION_ADMIN_PASSWORD
  if (value === undefined || value.length === 0) {
    throw new Error("PRODUCTION_ADMIN_PASSWORD is required")
  }
  return value
}

test("deployed fresh search remains read-only and exposes truthful evidence", async ({
  context,
  page,
}, testInfo) => {
  const consoleErrors: string[] = []
  const unsafeRequests: string[] = []
  const networkEvidence: string[] = []
  let authenticated = false

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text())
  })
  page.on("request", (request) => {
    const url = new URL(request.url())
    networkEvidence.push(`${request.method()} ${url.origin}${url.pathname}`)
    if (authenticated && !READ_ONLY_METHODS.has(request.method())) {
      unsafeRequests.push(`${request.method()} ${url.origin}${url.pathname}`)
    }
  })

  await page.goto("/login")
  await page.getByLabel("관리자 비밀번호").fill(requiredPassword())
  await page.getByRole("button", { name: "대시보드 열기" }).click()
  await page.waitForURL((url) => url.pathname === "/")
  authenticated = true

  const browserStorage = await page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
  }))
  expect(browserStorage, "인증 정보를 Web Storage에 저장하면 안 됩니다.").toEqual({
    local: [],
    session: [],
  })
  const cookies = await context.cookies()
  expect(
    cookies.some((cookie) => cookie.value.includes(requiredPassword())),
    "비밀번호가 쿠키에 남으면 안 됩니다.",
  ).toBe(false)

  await page.goto("/posts")
  await expect(page.getByRole("heading", { name: "최근 게시글", level: 1 })).toBeVisible()
  const firstTitle = page.locator(".post-main strong").first()
  await expect(firstTitle).toBeVisible()
  const literal = (await firstTitle.textContent())?.match(/[가-힣A-Za-z]{2,}/u)?.[0] ?? "시장"

  const trigger = page.getByRole("button", { name: "필터 열기" })
  if (await trigger.isVisible()) await trigger.click()
  await expect(page.getByRole("textbox", { name: "분류 키워드" })).toBeVisible()
  await expect(page.getByRole("textbox", { name: "글 검색" })).toBeVisible()
  await expect(page.getByLabel("기간")).toHaveValue("30d")
  await page.getByRole("textbox", { name: "글 검색" }).fill(literal)
  await page.getByRole("button", { name: "적용" }).click()
  await page.waitForURL(
    (url) => url.pathname === "/posts" && url.searchParams.get("search") === literal,
  )

  await expect(page.getByRole("list", { name: "적용 중인 필터" })).toContainText(`검색: ${literal}`)
  await expect(page.getByText("최신 원문")).toBeVisible()
  await expect(page.getByRole("list", { name: "소스별 최근 수집" })).toBeVisible()
  await expect(page.getByRole("navigation", { name: "게시글 페이지 이동" })).toBeVisible()

  const next = page.getByRole("link", { name: "다음 페이지" })
  if (await next.isVisible()) {
    const nextUrl = new URL((await next.getAttribute("href")) ?? "", page.url())
    expect(nextUrl.searchParams.get("search")).toBe(literal)
    await next.click()
    await expect(page.getByRole("link", { name: "이전 페이지" })).toBeVisible()
  } else {
    await expect(page.getByText("다음 페이지")).toHaveAttribute("aria-disabled", "true")
  }

  const original = page.getByRole("link", { name: /원문 열기/ }).first()
  await expect(original).toHaveAttribute("target", "_blank")
  expect(new URL((await original.getAttribute("href")) ?? "").protocol).toBe("https:")

  await testInfo.attach("read-only-network.json", {
    body: Buffer.from(JSON.stringify(networkEvidence.sort(), null, 2)),
    contentType: "application/json",
  })
  await testInfo.attach("redacted-surface.png", {
    body: await page.screenshot({
      mask: [page.locator("input"), page.locator(".post-main")],
      animations: "disabled",
      fullPage: true,
    }),
    contentType: "image/png",
  })

  expect(unsafeRequests, "로그인 후 쓰기 요청이 감지되었습니다.").toEqual([])
  expect(consoleErrors, "브라우저 console error가 감지되었습니다.").toEqual([])
})

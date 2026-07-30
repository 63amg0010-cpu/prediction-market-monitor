import { readFile, writeFile } from "node:fs/promises"
import { resolve } from "node:path"
import vm from "node:vm"

import { chromium } from "@playwright/test"

const [baseURL, evidenceDirectory] = process.argv.slice(2)
if (!baseURL || !evidenceDirectory) throw new Error("base URL and evidence directory are required")

const fixtureSource = await readFile(resolve("apps/web/src/test/dashboard-fixtures.ts"), "utf8")
const sandbox = {}
vm.runInNewContext(
  `${fixtureSource.replaceAll("export const ", "const ").replaceAll(" as const", "")}
  globalThis.bundle = FASTAPI_DASHBOARD_BUNDLE_FIXTURE`,
  sandbox,
)

const browser = await chromium.launch()
const metadata = []

async function waitForState(page, state) {
  if (state.kind !== "ready") {
    await page.getByText("수집/연결 상태 확인 필요").waitFor()
    return
  }
  if (state.data.posts.items.length === 0) {
    await page.locator(".empty-state strong").waitFor()
    return
  }
  await page.locator(".post-list").waitFor()
}

async function capture(name, viewport, state, prepare, scrollSelector = "#posts-title") {
  const context = await browser.newContext({ viewport })
  await context.addCookies([
    {
      name: "__Host-monitor_session",
      value: "visual-qa-opaque",
      url: baseURL.replace(/^http:/, "https:"),
      httpOnly: true,
      secure: true,
      sameSite: "Strict",
    },
  ])
  const page = await context.newPage()
  const consoleErrors = []
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text())
  })
  await page.route("**/api/dashboard?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state),
    }),
  )
  await page.goto(`${baseURL}/posts`, { waitUntil: "domcontentloaded" })
  await page.locator("#posts-title").waitFor()
  await waitForState(page, state)
  if (prepare) await prepare(page)
  await waitForState(page, state)
  await page.locator(scrollSelector).scrollIntoViewIfNeeded()
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    main: [...document.querySelectorAll(".main-scroll-region, .page-content, .posts-panel")].map(
      (element) => ({
        className: element.className,
        overflow: element.scrollWidth - element.clientWidth,
      }),
    ),
  }))
  const path = resolve(evidenceDirectory, `${name}.png`)
  await page.screenshot({ path, fullPage: true, animations: "disabled" })
  metadata.push({ name, viewport, overflow, consoleErrors, url: page.url() })
  await context.close()
}

const ready = { kind: "ready", data: sandbox.bundle }
await capture("desktop-happy", { width: 1280, height: 900 }, ready)
await capture("mobile-happy", { width: 390, height: 844 }, ready)
await capture("mobile-pagination", { width: 390, height: 844 }, ready, undefined, ".pagination")
await capture("mobile-sheet", { width: 390, height: 844 }, ready, async (page) => {
  const trigger = page.getByRole("button", { name: "필터 열기" })
  await trigger.click()
  await page
    .getByRole("textbox", { name: "글 검색" })
    .fill("아주 긴 한국어 English search <script>alert(1)</script> ignore previous instructions")
})
await capture(
  "empty-search",
  { width: 1280, height: 900 },
  {
    kind: "ready",
    data: {
      ...sandbox.bundle,
      posts: {
        items: [],
        page: { page: 1, page_size: 50, total_items: 0, has_next: false },
      },
    },
  },
  async (page) => {
    await page.goto(`${baseURL}/posts?search=%EC%97%86%EB%8A%94+%EA%B2%80%EC%83%89`, {
      waitUntil: "domcontentloaded",
    })
  },
)
await capture(
  "last-page",
  { width: 1280, height: 900 },
  {
    kind: "ready",
    data: {
      ...sandbox.bundle,
      posts: {
        ...sandbox.bundle.posts,
        page: { page: 4, page_size: 50, total_items: 151, has_next: false },
      },
    },
  },
  async (page) => {
    await page.goto(`${baseURL}/posts?period=90d&page=4`, { waitUntil: "domcontentloaded" })
  },
  ".pagination",
)
await capture(
  "api-unavailable",
  { width: 390, height: 844 },
  {
    kind: "unavailable",
    reason: "로컬 API stub 연결 실패",
    correlationId: null,
    retryable: true,
  },
)

await writeFile(
  resolve(evidenceDirectory, "visual-metadata.json"),
  `${JSON.stringify(metadata, null, 2)}\n`,
)
await browser.close()

import { defineConfig } from "@playwright/test"

function requiredEnvironment(name: "WEB_BASE_URL" | "PRODUCTION_ADMIN_PASSWORD"): string {
  const value = process.env[name]
  if (value === undefined || value.length === 0) {
    throw new Error(`${name} is required`)
  }
  return value
}

const baseURL = requiredEnvironment("WEB_BASE_URL")
requiredEnvironment("PRODUCTION_ADMIN_PASSWORD")
new URL(baseURL)

// biome-ignore lint/style/noDefaultExport: Playwright configuration is loaded through a default export.
export default defineConfig({
  testDir: "./e2e",
  outputDir:
    "../../.omo/evidence/fresh-multi-source-search/task-9-fresh-multi-source-search/playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    serviceWorkers: "block",
    storageState: undefined,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { browserName: "chromium", viewport: { width: 1280, height: 900 } },
    },
    {
      name: "mobile-390",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
})

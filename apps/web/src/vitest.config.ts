import { defineConfig } from "vitest/config"

export default defineConfig({
  oxc: {
    jsx: {
      importSource: "react",
      runtime: "automatic",
    },
  },
  test: {
    environment: "jsdom",
    exclude: [".next/**", "e2e/**", "node_modules/**"],
  },
})

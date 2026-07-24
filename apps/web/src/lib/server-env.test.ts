import { describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))

import { BoundaryError } from "./api-error"
import { readServerEnvironment } from "./server-env"

const validEnvironment: NodeJS.ProcessEnv = {
  API_BASE_URL: "https://api.example.test",
  BFF_CLIENT_CREDENTIAL: "a-secure-credential-with-at-least-32-characters",
  BFF_CREDENTIAL_VERSION: "20260723",
  NODE_ENV: "production",
  VERCEL_DEPLOYMENT_ID: "deployment-123",
}

describe("server environment boundary", () => {
  it("parses a supplied deployment environment", () => {
    expect(readServerEnvironment(validEnvironment)).toEqual({
      apiBaseUrl: "https://api.example.test",
      bffClientCredential: "a-secure-credential-with-at-least-32-characters",
      bffCredentialVersion: "20260723",
      deploymentIdentity: "deployment-123",
    })
  })

  it("rejects a supplied production environment with an insecure API URL", () => {
    const environment: NodeJS.ProcessEnv = {
      ...validEnvironment,
      API_BASE_URL: "http://localhost:3104",
    }

    expect(() => readServerEnvironment(environment)).toThrow(BoundaryError)
  })
})

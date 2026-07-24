import "server-only"

import { z } from "zod"

import { BoundaryError } from "./api-error"

const ServerEnvironmentSchema = z
  .object({
    API_BASE_URL: z.string().url(),
    BFF_CLIENT_CREDENTIAL: z.string().min(32),
    BFF_CREDENTIAL_VERSION: z.string().min(1),
    NODE_ENV: z.enum(["development", "test", "production"]),
    VERCEL_DEPLOYMENT_ID: z.string().min(1).optional(),
    VERCEL_URL: z.string().min(1).optional(),
  })
  .strict()

export type ServerEnvironment = {
  readonly apiBaseUrl: string
  readonly bffClientCredential: string
  readonly bffCredentialVersion: string
  readonly deploymentIdentity: string
}

type ServerProcessEnvironment = Pick<NodeJS.ProcessEnv, "NODE_ENV"> &
  Partial<
    Pick<
      NodeJS.ProcessEnv,
      | "API_BASE_URL"
      | "BFF_CLIENT_CREDENTIAL"
      | "BFF_CREDENTIAL_VERSION"
      | "VERCEL_DEPLOYMENT_ID"
      | "VERCEL_URL"
    >
  >

function allowsApiUrl(value: string, nodeEnvironment: string): boolean {
  const url = new URL(value)
  return (
    url.protocol === "https:" ||
    (nodeEnvironment !== "production" && ["localhost", "127.0.0.1"].includes(url.hostname))
  )
}

export function readServerEnvironment(
  environment: ServerProcessEnvironment = process.env,
): ServerEnvironment {
  const parsed = ServerEnvironmentSchema.safeParse({
    API_BASE_URL: environment.API_BASE_URL,
    BFF_CLIENT_CREDENTIAL: environment.BFF_CLIENT_CREDENTIAL,
    BFF_CREDENTIAL_VERSION: environment.BFF_CREDENTIAL_VERSION,
    NODE_ENV: environment.NODE_ENV,
    VERCEL_DEPLOYMENT_ID: environment.VERCEL_DEPLOYMENT_ID,
    VERCEL_URL: environment.VERCEL_URL,
  })
  if (!parsed.success) {
    throw new BoundaryError("configuration", "server identity is not configured")
  }
  if (!allowsApiUrl(parsed.data.API_BASE_URL, parsed.data.NODE_ENV)) {
    throw new BoundaryError("configuration", "API URL must use HTTPS")
  }
  const deploymentIdentity =
    parsed.data.VERCEL_DEPLOYMENT_ID ??
    parsed.data.VERCEL_URL ??
    (parsed.data.NODE_ENV === "production" ? null : "local-development")
  if (deploymentIdentity === null) {
    throw new BoundaryError("configuration", "deployment identity is not configured")
  }
  return {
    apiBaseUrl: parsed.data.API_BASE_URL,
    bffClientCredential: parsed.data.BFF_CLIENT_CREDENTIAL,
    bffCredentialVersion: parsed.data.BFF_CREDENTIAL_VERSION,
    deploymentIdentity,
  }
}

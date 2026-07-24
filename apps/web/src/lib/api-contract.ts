import { z } from "zod"

export const BffScopeSchema = z.enum(["bff:auth", "bff:read", "admin:command"])

export const BffExchangeResponseSchema = z
  .object({
    access_token: z.string().min(1),
    token_type: z.literal("Bearer"),
    expires_at: z.string().datetime({ offset: true }),
    scope: z.array(BffScopeSchema).min(1),
  })
  .strict()

export const AdminSessionResponseSchema = z
  .object({
    session_token: z.string().min(1).nullable().optional(),
    expires_at: z.string().datetime({ offset: true }),
    csrf_token: z.string().min(1),
    rotated: z.boolean(),
  })
  .strict()

export const BrowserSessionSchema = z
  .object({
    authenticated: z.literal(true),
    expiresAt: z.string().datetime({ offset: true }),
    csrfToken: z.string().min(1),
  })
  .strict()

export type AdminSession = {
  readonly sessionToken: string | null
  readonly expiresAt: string
  readonly csrfToken: string
  readonly rotated: boolean
}

export function normalizeAdminSession(
  value: z.infer<typeof AdminSessionResponseSchema>,
): AdminSession {
  return {
    sessionToken: value.session_token ?? null,
    expiresAt: value.expires_at,
    csrfToken: value.csrf_token,
    rotated: value.rotated,
  }
}

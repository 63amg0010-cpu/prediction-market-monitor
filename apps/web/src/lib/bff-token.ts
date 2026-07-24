import { BoundaryError } from "./api-error"

export const BFF_READ_SCOPES = ["bff:auth", "bff:read"] as const
export const ADMIN_COMMAND_SCOPES = ["admin:command"] as const

export type BffScopeSet = typeof BFF_READ_SCOPES | typeof ADMIN_COMMAND_SCOPES

export type ExchangedToken = {
  readonly accessToken: string
  readonly expiresAt: string
  readonly scope: readonly string[]
}

type TokenProviderOptions = {
  readonly exchange: (scopes: BffScopeSet) => Promise<ExchangedToken>
  readonly now: () => number
}

export interface BffTokenProvider {
  get(scopes: BffScopeSet): Promise<string>
}

type CacheEntry = {
  readonly accessToken: string
  readonly cacheUntil: number
}

const MAX_CACHE_MILLISECONDS = 240_000
const EXPIRY_SAFETY_MILLISECONDS = 15_000

function scopeKey(scopes: BffScopeSet): string {
  return scopes.join(" ")
}

function exactScopes(expected: BffScopeSet, actual: readonly string[]): boolean {
  return (
    expected.length === actual.length && expected.every((scope, index) => scope === actual[index])
  )
}

export function createBffTokenProvider(options: TokenProviderOptions): BffTokenProvider {
  const cache = new Map<string, CacheEntry>()
  const inFlight = new Map<string, Promise<string>>()

  return {
    async get(scopes): Promise<string> {
      const key = scopeKey(scopes)
      const now = options.now()
      const cached = cache.get(key)
      if (cached !== undefined && cached.cacheUntil > now) {
        return cached.accessToken
      }
      const pending = inFlight.get(key)
      if (pending !== undefined) {
        return pending
      }
      const request = options
        .exchange(scopes)
        .then((token) => {
          if (!exactScopes(scopes, token.scope)) {
            throw new BoundaryError("invalid_response", "service token scope mismatch")
          }
          const expiresAt = Date.parse(token.expiresAt)
          const cacheUntil = Math.min(
            now + MAX_CACHE_MILLISECONDS,
            expiresAt - EXPIRY_SAFETY_MILLISECONDS,
          )
          if (!Number.isFinite(expiresAt) || cacheUntil <= now) {
            throw new BoundaryError("invalid_response", "service token expiry is invalid")
          }
          cache.set(key, { accessToken: token.accessToken, cacheUntil })
          return token.accessToken
        })
        .finally(() => inFlight.delete(key))
      inFlight.set(key, request)
      return request
    },
  }
}

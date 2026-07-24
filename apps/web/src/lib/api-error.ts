import { z } from "zod"

const ErrorEnvelopeSchema = z
  .object({
    error: z
      .object({
        code: z.string().min(1),
        message: z.string().min(1),
        correlation_id: z.string().min(1),
      })
      .strict(),
  })
  .strict()

export type BoundaryErrorCode =
  | "configuration"
  | "network"
  | "upstream"
  | "invalid_response"
  | "session_required"

export class BoundaryError extends Error {
  readonly name = "BoundaryError"

  constructor(
    readonly code: BoundaryErrorCode,
    message: string,
    readonly context: {
      readonly status: number | null
      readonly correlationId: string | null
    } = { status: null, correlationId: null },
  ) {
    super(message)
  }

  get status(): number | null {
    return this.context.status
  }

  get correlationId(): string | null {
    return this.context.correlationId
  }
}

export async function upstreamError(response: Response): Promise<BoundaryError> {
  const raw: unknown = await response.json().catch(() => null)
  const parsed = ErrorEnvelopeSchema.safeParse(raw)
  if (parsed.success) {
    return new BoundaryError("upstream", parsed.data.error.message, {
      status: response.status,
      correlationId: parsed.data.error.correlation_id,
    })
  }
  return new BoundaryError("upstream", "upstream request failed", {
    status: response.status,
    correlationId: response.headers.get("x-correlation-id"),
  })
}

export function publicError(error: BoundaryError): {
  readonly status: number
  readonly body: {
    readonly error: string
    readonly correlationId: string | null
    readonly retryable: boolean
  }
} {
  const retryable =
    error.code === "network" || error.code === "configuration" || (error.status ?? 0) >= 500
  const status =
    error.status === 401 || error.status === 403 || error.status === 429 ? error.status : 503
  return {
    status,
    body: {
      error: retryable
        ? "서비스 연결을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요."
        : "요청을 완료할 수 없습니다. 입력과 로그인 상태를 확인해 주세요.",
      correlationId: error.correlationId,
      retryable,
    },
  }
}

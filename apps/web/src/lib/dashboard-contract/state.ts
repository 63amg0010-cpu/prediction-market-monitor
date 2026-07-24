import { z } from "zod"

import { DashboardResponseSchema } from "./dashboard"
import { PostPageSchema } from "./posts"
import { ReportPageSchema } from "./reports"

export const DashboardBundleSchema = z
  .object({
    dashboard: DashboardResponseSchema,
    posts: PostPageSchema,
    reports: ReportPageSchema,
  })
  .strict()

const FailureStateFields = {
  reason: z.string().min(1),
  correlationId: z.string().nullable(),
  retryable: z.boolean(),
} as const

export const DashboardStateSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("loading") }).strict(),
  z.object({ kind: z.literal("ready"), data: DashboardBundleSchema }).strict(),
  z.object({ kind: z.literal("invalid_request"), ...FailureStateFields }).strict(),
  z.object({ kind: z.literal("unavailable"), ...FailureStateFields }).strict(),
])

export type Coverage = { readonly numerator: number; readonly denominator: number }
export type Metric =
  | {
      readonly kind: "available"
      readonly value: number
      readonly unit: string
      readonly coverage: Coverage | null
    }
  | {
      readonly kind: "partial"
      readonly value: number
      readonly unit: string
      readonly coverage: Coverage | null
      readonly reason: string
    }
  | {
      readonly kind: "loading" | "pending" | "error" | "blocked" | "null" | "unknown"
      readonly reason: string
    }

export type DashboardData = z.infer<typeof DashboardBundleSchema>
export type DashboardState = z.infer<typeof DashboardStateSchema>

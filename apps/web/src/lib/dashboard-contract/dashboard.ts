import { z } from "zod"

import { CountrySchema, DecimalStringSchema, OutcomeSchema, TimestampSchema } from "./shared"

const MentionSummarySchema = z
  .object({
    current_count: z.number().int().nonnegative(),
    previous_count: z.number().int().nonnegative(),
    delta: z.number().int(),
    delta_rate: DecimalStringSchema.nullable(),
    status: OutcomeSchema,
  })
  .strict()

const AnalysisSummarySchema = z
  .object({
    candidate_count: z.number().int().nonnegative(),
    valid_count: z.number().int().nonnegative(),
    pending_count: z.number().int().nonnegative(),
    blocked_count: z.number().int().nonnegative(),
    coverage: DecimalStringSchema.nullable(),
    positive_count: z.number().int().nonnegative(),
    neutral_count: z.number().int().nonnegative(),
    negative_count: z.number().int().nonnegative(),
    unknown_sentiment_count: z.number().int().nonnegative(),
    status: OutcomeSchema,
  })
  .strict()

const EngagementSummarySchema = z
  .object({
    comments_sum: z.number().int().nullable(),
    comments_known_count: z.number().int().nonnegative(),
    comments_unknown_count: z.number().int().nonnegative(),
    score_sum: z.number().int().nullable(),
    score_known_count: z.number().int().nonnegative(),
    score_unknown_count: z.number().int().nonnegative(),
    status: OutcomeSchema,
  })
  .strict()

const OperationsSummarySchema = z
  .object({
    last_complete_collection_at: TimestampSchema.nullable(),
    last_analysis_at: TimestampSchema.nullable(),
    pending_analysis_count: z.number().int().nonnegative(),
    blocked_analysis_count: z.number().int().nonnegative(),
    collection_status: OutcomeSchema,
    analysis_status: OutcomeSchema,
  })
  .strict()

export const SourceStatusSchema = z
  .object({
    source_id: z.string().uuid(),
    display_name: z.string().min(1),
    country: CountrySchema,
    enabled: z.boolean(),
    status: OutcomeSchema,
    latest_successful_run_at: TimestampSchema.nullable(),
    visible_publication_sequence: z.number().int().positive().nullable().optional(),
    failure_code: z.string().min(1).nullable().optional(),
    retry_eligible: z.boolean(),
    retry_block_reason: z.string().min(1).nullable(),
  })
  .strict()

export const DashboardResponseSchema = z
  .object({
    generated_at: TimestampSchema,
    mentions: MentionSummarySchema,
    analysis: AnalysisSummarySchema,
    engagement: EngagementSummarySchema,
    operations: OperationsSummarySchema,
    sources: z.array(SourceStatusSchema),
  })
  .strict()

export type DashboardResponse = z.infer<typeof DashboardResponseSchema>
export type SourceStatus = z.infer<typeof SourceStatusSchema>

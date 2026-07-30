import { z } from "zod"

import { CountrySchema, DecimalStringSchema, Sha256HexSchema, TimestampSchema } from "./shared"

const PageInfoSchema = z
  .object({
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
    total_items: z.number().int().nonnegative(),
    has_next: z.boolean(),
  })
  .strict()

const HighlightSchema = z
  .object({
    category: z.string().min(1),
    primary_count: z.number().int().nonnegative(),
    comparison_count: z.number().int().nonnegative(),
    delta: z.number().int(),
    delta_rate_numerator: z.number().int(),
    delta_rate_denominator: z.number().int().nonnegative(),
    delta_rate_decimal: DecimalStringSchema.nullable(),
    net_sentiment: z.number().int(),
  })
  .strict()

const RisingKeywordSchema = z
  .object({
    phrase: z.string().min(1),
    primary_count: z.number().int().nonnegative(),
    comparison_count: z.number().int().nonnegative(),
    delta: z.number().int(),
    delta_rate_numerator: z.number().int(),
    delta_rate_denominator: z.number().int().nonnegative(),
    delta_rate_decimal: DecimalStringSchema.nullable(),
  })
  .strict()

const SourceCoverageSchema = z
  .object({
    role: z.enum(["primary", "comparison"]),
    source_id: z.string().uuid(),
    country: CountrySchema,
    platform: z.enum(["reddit", "dcinside", "toss_securities", "naver_finance", "manifold"]),
    community: z.string().min(1),
    expected: z.boolean(),
    enabled: z.boolean(),
    collection_status: z.enum([
      "complete",
      "partial",
      "missing",
      "skipped_policy",
      "skipped_quota",
      "failed_retryable",
      "failed_terminal",
      "unauthorized",
    ]),
    expected_run_count: z.number().int().nonnegative(),
    successful_run_count: z.number().int().nonnegative(),
    failed_run_count: z.number().int().nonnegative(),
    skipped_run_count: z.number().int().nonnegative(),
    candidate_count: z.number().int().nonnegative(),
    valid_analysis_count: z.number().int().nonnegative(),
    pending_count: z.number().int().nonnegative(),
    relevant_count: z.number().int().nonnegative(),
    cutoff_publication_sequence: z.number().int().nonnegative().nullable(),
    cutoff_publication_manifest_id: z.string().uuid().nullable(),
    cutoff_publication_manifest_hash: Sha256HexSchema.nullable(),
    latest_successful_run_started_at: TimestampSchema.nullable(),
    latest_successful_run_finished_at: TimestampSchema.nullable(),
    latest_publication_committed_at: TimestampSchema.nullable(),
    latest_attempt_finished_at: TimestampSchema.nullable(),
    status_observed_at: TimestampSchema.nullable(),
    coverage_numerator: z.number().int().nonnegative(),
    coverage_denominator: z.number().int().nonnegative(),
    coverage_decimal: DecimalStringSchema.nullable(),
  })
  .strict()

export const ReportItemSchema = z
  .object({
    id: z.string().uuid(),
    report_date_seoul: z.string().date(),
    revision: z.number().int().positive(),
    status: z.enum(["complete", "partial"]),
    candidate_count: z.number().int().nonnegative(),
    relevant_count: z.number().int().nonnegative(),
    pending_count: z.number().int().nonnegative(),
    analysis_coverage: DecimalStringSchema.nullable(),
    comments_sum: z.number().int().nullable(),
    score_sum: z.number().int().nullable(),
    highlights: z.array(HighlightSchema),
    rising_keywords: z.array(RisingKeywordSchema),
    source_coverage: z.array(SourceCoverageSchema),
    manifest_id: z.string().uuid(),
    input_set_hash: Sha256HexSchema,
    manifest_payload_sha256: Sha256HexSchema,
    report_payload_sha256: Sha256HexSchema,
    reproduction_status: z.enum(["verified", "unverified"]),
    created_at: TimestampSchema,
  })
  .strict()

export const ReportPageSchema = z
  .object({ items: z.array(ReportItemSchema), page: PageInfoSchema })
  .strict()

export type ReportItem = z.infer<typeof ReportItemSchema>
export type ReportPage = z.infer<typeof ReportPageSchema>

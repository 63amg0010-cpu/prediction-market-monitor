import { z } from "zod"

import { CountrySchema, OutcomeSchema, TimestampSchema } from "./shared"

const PageInfoSchema = z
  .object({
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
    total_items: z.number().int().nonnegative(),
    has_next: z.boolean(),
  })
  .strict()

export const PostItemSchema = z
  .object({
    id: z.string().uuid(),
    source_id: z.string().uuid(),
    source_name: z.string().min(1),
    country: CountrySchema,
    title: z.string().min(1),
    original_url: z.string().url(),
    published_at: TimestampSchema,
    analysis_state: z.enum([
      "valid",
      "pending",
      "blocked_capability",
      "failed_retryable",
      "failed_terminal",
      "invalid_output",
    ]),
    relevance: z.boolean().nullable(),
    sentiment: z.enum(["positive", "neutral", "negative"]).nullable(),
    comments_count: z.number().int().nonnegative().nullable().optional(),
    score: z.number().int().nullable().optional(),
    engagement_status: OutcomeSchema,
  })
  .strict()

export const PostPageSchema = z
  .object({ items: z.array(PostItemSchema), page: PageInfoSchema })
  .strict()

export type PostItem = z.infer<typeof PostItemSchema>
export type PostPage = z.infer<typeof PostPageSchema>

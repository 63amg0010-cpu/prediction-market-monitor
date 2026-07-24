import { z } from "zod"

export const CollectionRetryBrowserRequestSchema = z
  .object({
    requestId: z.string().uuid(),
    sourceId: z.string().uuid(),
  })
  .strict()

export const CollectionRetryResponseSchema = z
  .object({
    command_id: z.string().uuid(),
    created: z.boolean(),
  })
  .strict()

export type CollectionRetryResponse = z.infer<typeof CollectionRetryResponseSchema>

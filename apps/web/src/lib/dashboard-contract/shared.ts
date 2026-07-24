import { z } from "zod"

export const OutcomeSchema = z.enum([
  "success",
  "pending",
  "blocked",
  "partial",
  "error",
  "unknown",
])

export const CountrySchema = z.enum(["kr", "us"])
export const DecimalStringSchema = z
  .string()
  .regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/, "expected a JSON decimal string")
export const TimestampSchema = z.string().datetime({ offset: true })
export const Sha256HexSchema = z
  .string()
  .regex(/^[0-9a-f]{64}$/, "expected a lowercase SHA-256 hex")

export type Outcome = z.infer<typeof OutcomeSchema>

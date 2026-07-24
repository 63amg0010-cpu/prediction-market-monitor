import { describe, expect, it } from "vitest"
import { FASTAPI_DASHBOARD_BUNDLE_FIXTURE } from "../test/dashboard-fixtures"
import { DashboardResponseSchema, type Outcome } from "./dashboard-contract"
import { dashboardMetrics } from "./dashboard-presenter"

const base = DashboardResponseSchema.parse(FASTAPI_DASHBOARD_BUNDLE_FIXTURE.dashboard)
const unavailableStatuses = ["pending", "error", "blocked", "unknown"] as const

function withMentions(status: Outcome, deltaRate = base.mentions.delta_rate) {
  return DashboardResponseSchema.parse({
    ...base,
    mentions: { ...base.mentions, status, delta_rate: deltaRate },
  })
}

function withAnalysis(
  status: Outcome,
  counts = { positive_count: 5, neutral_count: 3, negative_count: 2 },
) {
  return DashboardResponseSchema.parse({
    ...base,
    analysis: { ...base.analysis, status, ...counts },
  })
}

function withEngagement(status: Outcome, commentsSum = base.engagement.comments_sum) {
  return DashboardResponseSchema.parse({
    ...base,
    engagement: { ...base.engagement, status, comments_sum: commentsSum },
  })
}

describe("dashboard metric state semantics", () => {
  it.each(unavailableStatuses)("preserves mention %s without collapsing to unknown", (status) => {
    expect(dashboardMetrics(withMentions(status)).mentions.kind).toBe(status)
  })

  it("keeps partial and null mention values distinct", () => {
    expect(dashboardMetrics(withMentions("partial")).mentions.kind).toBe("partial")
    expect(dashboardMetrics(withMentions("success", null)).delta.kind).toBe("null")
  })

  it.each(unavailableStatuses)("preserves analysis %s before inspecting zero counts", (status) => {
    expect(
      dashboardMetrics(
        withAnalysis(status, { positive_count: 0, neutral_count: 0, negative_count: 0 }),
      ).sentiment.kind,
    ).toBe(status)
  })

  it("keeps analysis partial and null values distinct", () => {
    expect(dashboardMetrics(withAnalysis("partial")).sentiment.kind).toBe("partial")
    expect(
      dashboardMetrics(
        withAnalysis("success", { positive_count: 0, neutral_count: 0, negative_count: 0 }),
      ).sentiment.kind,
    ).toBe("null")
  })

  it.each(unavailableStatuses)(
    "preserves engagement %s before inspecting null totals",
    (status) => {
      expect(dashboardMetrics(withEngagement(status, null)).engagement.kind).toBe(status)
    },
  )

  it("keeps a successful null engagement total explicit", () => {
    expect(dashboardMetrics(withEngagement("success", null)).engagement.kind).toBe("null")
  })
})

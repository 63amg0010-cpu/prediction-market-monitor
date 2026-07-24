import { describe, expect, it } from "vitest"

import {
  FASTAPI_DASHBOARD_FIXTURE,
  FASTAPI_POST_PAGE_FIXTURE,
  FASTAPI_REPORT_PAGE_FIXTURE,
} from "../test/dashboard-fixtures"
import {
  DashboardBundleSchema,
  DashboardResponseSchema,
  DashboardStateSchema,
  OutcomeSchema,
  PostItemSchema,
  PostPageSchema,
  ReportItemSchema,
  ReportPageSchema,
  SourceStatusSchema,
} from "./dashboard-contract"

describe("FastAPI dashboard boundary", () => {
  it("preserves every public parser at the legacy module path", () => {
    const dashboard = DashboardResponseSchema.parse(FASTAPI_DASHBOARD_FIXTURE)
    const posts = PostPageSchema.parse(FASTAPI_POST_PAGE_FIXTURE)
    const reports = ReportPageSchema.parse(FASTAPI_REPORT_PAGE_FIXTURE)

    expect(DashboardBundleSchema.parse({ dashboard, posts, reports })).toEqual({
      dashboard,
      posts,
      reports,
    })
    expect(PostItemSchema.parse(FASTAPI_POST_PAGE_FIXTURE.items[0])).toEqual(posts.items[0])
    expect(ReportItemSchema.parse(FASTAPI_REPORT_PAGE_FIXTURE.items[0])).toEqual(reports.items[0])
    expect(SourceStatusSchema.parse(FASTAPI_DASHBOARD_FIXTURE.sources[0])).toEqual(
      dashboard.sources[0],
    )
    expect(OutcomeSchema.parse("partial")).toBe("partial")
    expect(
      DashboardStateSchema.parse({ kind: "ready", data: { dashboard, posts, reports } }),
    ).toEqual({
      kind: "ready",
      data: { dashboard, posts, reports },
    })
  })

  it("parses the current dashboard projection without inventing aggregate fields", () => {
    const parsed = DashboardResponseSchema.parse(FASTAPI_DASHBOARD_FIXTURE)

    expect(parsed.generated_at).toBe("2026-07-22T03:00:00Z")
    expect(parsed.mentions.current_count).toBe(12)
    expect(parsed.analysis.coverage).toBe("0.8333333333")
    expect(parsed.engagement.comments_sum).toBe(42)
    expect(parsed.operations.collection_status).toBe("success")
    expect(parsed.sources).toHaveLength(2)
  })

  it("rejects backend fields whose scalar representation drifts from OpenAPI", () => {
    const result = DashboardResponseSchema.safeParse({
      ...FASTAPI_DASHBOARD_FIXTURE,
      mentions: { ...FASTAPI_DASHBOARD_FIXTURE.mentions, delta_rate: 0.5 },
    })

    expect(result.success).toBe(false)
  })

  it("keeps posts and reports outside the dashboard response contract", () => {
    const result = DashboardResponseSchema.safeParse({
      ...FASTAPI_DASHBOARD_FIXTURE,
      posts: FASTAPI_POST_PAGE_FIXTURE,
      reports: FASTAPI_REPORT_PAGE_FIXTURE,
    })

    expect(result.success).toBe(false)
  })
})

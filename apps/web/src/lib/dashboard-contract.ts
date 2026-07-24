import type { components } from "./api.generated"
import type { DashboardResponse } from "./dashboard-contract/dashboard"
import type { PostPage } from "./dashboard-contract/posts"
import type { ReportPage } from "./dashboard-contract/reports"

export type { DashboardResponse, SourceStatus } from "./dashboard-contract/dashboard"
export { DashboardResponseSchema, SourceStatusSchema } from "./dashboard-contract/dashboard"
export type { PostItem } from "./dashboard-contract/posts"
export { PostItemSchema, PostPageSchema } from "./dashboard-contract/posts"
export type { ReportItem } from "./dashboard-contract/reports"
export { ReportItemSchema, ReportPageSchema } from "./dashboard-contract/reports"
export type { Outcome } from "./dashboard-contract/shared"
export { OutcomeSchema } from "./dashboard-contract/shared"
export type {
  Coverage,
  DashboardData,
  DashboardState,
  Metric,
} from "./dashboard-contract/state"
export { DashboardBundleSchema, DashboardStateSchema } from "./dashboard-contract/state"

type GeneratedDashboardResponse = components["schemas"]["DashboardResponse"]
type GeneratedPostPage = components["schemas"]["PostPage"]
type GeneratedReportPage = components["schemas"]["ReportPage"]
type GeneratedAcceptedByRuntime<Generated, RuntimeOutput> = [Generated] extends [RuntimeOutput]
  ? true
  : false
type AssertCompatible<Value extends true> = Value

export type DashboardContractMatchesGenerated = AssertCompatible<
  GeneratedAcceptedByRuntime<GeneratedDashboardResponse, DashboardResponse>
>
export type PostContractMatchesGenerated = AssertCompatible<
  GeneratedAcceptedByRuntime<GeneratedPostPage, PostPage>
>
export type ReportContractMatchesGenerated = AssertCompatible<
  GeneratedAcceptedByRuntime<GeneratedReportPage, ReportPage>
>

import type { Outcome, ReportItem } from "./dashboard-contract"

export function reportOutcome(report: ReportItem | undefined): Outcome {
  if (report === undefined) return "unknown"
  return report.status === "complete" && report.reproduction_status === "verified"
    ? "success"
    : "partial"
}
